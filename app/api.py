"""REST API 服务（FastAPI）：将 HerbDemo 封装为 HTTP 接口。

启动:
    python main.py --mode serve --port 8000
    python app/api.py                  （等价，端口取环境变量 API_PORT 或 8000）

接口:
    GET  /health        健康检查
    POST /predict       图片+文本识别（multipart/form-data；image 可留空做纯文本检索）
    POST /search        纯文本特性检索（JSON）
    POST /explain       Grad-CAM 热图（multipart，返回 PNG 图片，说明信息以 URL 编码放 X-Explain-Info 头）
    POST /chat          外部 LLM 对话解释（multipart：image 可选 + question + history 可选 JSON，支持多轮）

依赖:
    pip install fastapi uvicorn python-multipart
"""
import io
import json
import os
import sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import Response
from PIL import Image

from app.gradio_app import HerbDemo
from app.llm_client import LLMClient, LLMError
from utils.config import load_config

app = FastAPI(
    title="中草药多模态识别 API",
    description="本地 Swin+BERT 多模态识别 + 知识图谱。"
                "POST /predict 上传图片(可选)与文本(可选)，返回 Top-5 与药性/相似药/方剂信息。",
    version="1.0.0",
)
_demo: HerbDemo = None
_llm: LLMClient = None

# 中医药专家 system 提示词：要求 LLM 基于识别结果作答、区分来源与置信度、禁止编造
_SYSTEM_PROMPT = (
    "你是一位资深的中医药专家，擅长中草药识别、药性解析与方剂配伍。\n"
    "回答规则：\n"
    "1. 严格基于用户提供的「本地识别结果」与知识图谱信息作答，"
    "不要凭空编造药性、归经、功效或方剂。\n"
    "2. 明确区分信息来源：识别结果来自本地模型/知识图谱，"
    "其余补充知识需标注「参考中医典籍」；信息不足时直接说明「不确定」，"
    "并给出保守的鉴别建议。\n"
    "3. 涉及用法用量时给出常见参考范围，并提醒需在执业中医师指导下使用。\n"
    "4. 使用简体中文，条理清晰，面向普通用户可读。\n"
    "5. 若回答涉及有毒药材（大毒/有毒/小毒/微毒）或任何用药建议，"
    "必须在结尾明确警告：切勿自行用药，须在执业中医师指导下使用。"
)


# 医疗风险提示与科普免责声明（安全红线：所有用户可见输出必须附带）
_DISCLAIMER = (
    "⚠️ **医疗风险提示**：以上内容由本地识别模型与知识图谱自动生成，"
    "仅供**科普与学习参考**，不构成任何医疗诊断、处方或用药建议。"
    "中草药辨识与用药因人因证而异，请务必咨询**执业中医师或药师**，切勿自行用药。"
)


def get_demo() -> HerbDemo:
    """懒加载单例：首次请求才初始化模型（Swin+BERT 加载较慢）。"""
    global _demo
    if _demo is None:
        cfg = load_config(os.environ.get(
            "CONFIG", "experiments/configs/default_config.yaml"))
        ckpt = os.environ.get("CKPT", "experiments/checkpoints/best_model.pth")
        _demo = HerbDemo(cfg, ckpt)
    return _demo


def get_llm() -> LLMClient:
    """懒加载单例：LLM 客户端（环境变量优先，未配 key 时 available=False）。"""
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def _local_answer(pred: dict) -> str:
    """无 LLM 时的降级回答：基于本地识别结果拼装自然语言。"""
    top5 = pred.get("top5") or []
    if not top5:
        return pred.get("message", "未识别到相关药材，请补充图片或更明确的特性描述。")
    if pred.get("mode") == "image":
        lines = ["【本地识别结果】", *[
            f"{i}. {it['name']}（置信度 {it['prob'] * 100:.1f}%）"
            for i, it in enumerate(top5, 1)]]
    else:
        lines = ["【本地特性检索 Top-5】", *[
            f"{i}. {it['name']}（匹配度 {it.get('score', '-')}）"
            for i, it in enumerate(top5, 1)]]
    if pred.get("kg_info"):
        lines += ["", "【药性说明（知识图谱）】", pred["kg_info"]]
    if pred.get("similar"):
        lines += ["", "【相似药推荐】", "、".join(
            s["name"] for s in pred["similar"])]
    if pred.get("formula"):
        lines += ["", "【方剂推荐】", *[
            f"- {r['herb']}（依据：{r['reason']}）" for r in pred["formula"]]]
    if pred.get("classic_formulas"):
        lines += ["", "【经典方剂参考】"]
        for f in pred["classic_formulas"]:
            lines.append(f"- {f['name']}（{f.get('source', '')}）：{f.get('effects', '')}")
            if f.get("warning"):
                lines.append(f"  ⚠️ {f['warning']}")
    contra = pred.get("contraindications") or {}
    contra_parts = []
    if contra.get("incompatible"):
        contra_parts.append("、".join(contra["incompatible"]) + "（十八反）")
    if contra.get("restraint"):
        contra_parts.append("、".join(contra["restraint"]) + "（十九畏）")
    if contra_parts:
        lines += ["", "【配伍风险提示】" + "；".join(contra_parts)
                  + "（方剂推荐已自动规避，含禁忌配伍的组方不可使用）"]
    lines += ["", _DISCLAIMER]
    return "\n".join(lines)


def _parse_history(raw: str, max_turns: int = 10) -> list:
    """解析多轮对话历史 JSON 字符串，仅保留 user/assistant，最多取最近 max_turns 轮。"""
    if not raw or not raw.strip():
        return []
    try:
        hist = json.loads(raw)
        if not isinstance(hist, list):
            return []
    except (json.JSONDecodeError, ValueError):
        return []
    cleaned = [m for m in hist if isinstance(m, dict)
               and m.get("role") in ("user", "assistant")
               and isinstance(m.get("content"), str) and m["content"].strip()]
    return cleaned[-(max_turns * 2):]  # user + assistant 各算一轮


@app.get("/health")
def health():
    demo = get_demo()
    llm = get_llm()
    return {"status": "ok", "num_classes": len(demo.idx2label),
            "device": str(demo.device),
            "llm": {"available": llm.available,
                    "model": llm.model if llm.available else None}}


@app.post("/predict")
async def predict(image: UploadFile = None, text: str = Form("")):
    """图片+文本识别。image 留空则退化为纯文本特性检索。"""
    demo = get_demo()
    img = None
    if image is not None:
        data = await image.read()
        if not data:
            return {"error": "empty_image", "message": "上传的图片为空。"}
        img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    return demo.predict_json(img, text)


@app.post("/search")
async def search(payload: dict):
    """纯文本特性检索：性味/归经/功效，返回所有符合的中草药。"""
    demo = get_demo()
    text = (payload or {}).get("text", "")
    return {"query": text, "result": demo.search_text(text),
            "disclaimer": _DISCLAIMER}


@app.post("/explain")
async def explain(image: UploadFile, text: str = Form("")):
    """Grad-CAM 热图：返回 PNG 图片，说明信息在响应头 X-Explain-Info。"""
    demo = get_demo()
    data = await image.read()
    img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    heatmap, info = demo.explain(img, text)
    if heatmap is None:
        return {"error": "explain_failed", "message": info}
    buf = io.BytesIO()
    Image.fromarray(heatmap).save(buf, format="PNG")
    # HTTP 头只支持 latin-1，中文说明需 URL 编码（客户端 unquote 还原）
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"X-Explain-Info": quote(info)})


@app.post("/chat")
async def chat(image: UploadFile = None, question: str = Form(""),
               history: str = Form("[]")):
    """外部 LLM 对话解释：本地识别 → 组装多轮上下文 → 调用 LLM 生成回答。

    请求（multipart/form-data）:
      question  必填，用户问题（如"这是什么药材？"）
      image     可选，草药图片；有图走多模态识别，无图走特性检索 Top-5
      history   可选，多轮对话历史 JSON 字符串:
                [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]

    返回:
      answer      LLM 回答（llm=disabled/error 时为本地降级回答）
      llm         状态: ok / disabled（未配 key）/ error（调用失败）
      llm_model   使用的模型名
      mode / top5 / kg_info / similar / formula   本地识别结果（与 /predict 一致）
    """
    demo = get_demo()
    question = (question or "").strip()
    if not question:
        return {"error": "empty_question", "message": "请输入问题 question。"}

    # 1) 接收图片：有图走多模态识别（question 仅作为用户问题，不污染分类文本）
    img = None
    if image is not None:
        data = await image.read()
        if data:
            img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))

    # 2) 构建本地识别上下文：有图走图片识别（图文混合问答），
    #    无图则从自然语言问题提取药材名，提取不到时回退特性检索 Top-5
    context, herbs, pred = demo.build_chat_context(question, img)
    # 2.5) RAG 知识库检索依据（首次调用懒加载本地 BERT，失败不影响主流程）
    rag_text, rag_sources = "", []
    try:
        if demo.retriever is None:
            from app.rag_retriever import RAGRetriever
            demo.retriever = RAGRetriever(demo.kg)
        rag_text, rag_sources = demo.retriever.retrieve(question, herbs)
    except Exception as e:
        print(f"[RAG] 检索失败（继续对话）: {e}")
    user_content = f"【本地识别结果】\n{context}\n\n【用户问题】\n{question}"
    if rag_text:
        user_content = (f"【本地识别结果】\n{context}\n\n"
                        f"【知识库检索依据（可引用，勿编造依据外的信息）】\n{rag_text}\n\n"
                        f"【用户问题】\n{question}")
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages += _parse_history(history)
    messages.append({"role": "user", "content": user_content})

    llm = get_llm()
    if not llm.available:
        return {
            "answer": f"{context}\n\n（注：未配置 ZHIPU_API_KEY，以上为本地知识图谱结果；"
                      f"配置后可获得更自然的对话解释。）\n\n{_DISCLAIMER}",
            "llm": "disabled",
            "llm_model": llm.model,
            "rag_sources": rag_sources[:4],
            "disclaimer": _DISCLAIMER,
            **pred,
        }
    try:
        answer = llm.chat(messages)
        return {"answer": f"{answer}\n\n{_DISCLAIMER}", "llm": "ok",
                "llm_model": llm.model, "disclaimer": _DISCLAIMER,
                "rag_sources": rag_sources[:4], **pred}
    except LLMError as e:
        return {
            "answer": f"{context}\n\n（注：LLM 调用失败：{e}。以上为本地知识图谱结果。）"
                      f"\n\n{_DISCLAIMER}",
            "llm": "error",
            "llm_model": llm.model,
            "error_detail": str(e),
            "disclaimer": _DISCLAIMER,
            "rag_sources": rag_sources[:4],
            **pred,
        }


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", 8000))
    uvicorn.run(app, host="127.0.0.1", port=port)
