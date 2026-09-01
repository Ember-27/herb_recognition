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
from typing import List
from fastapi import FastAPI, File, Form, UploadFile, Body, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from app.gradio_app import HerbDemo
from app.llm_client import LLMClient, LLMError
from utils.config import load_config

# 新中式前端静态目录（web/index.html + style.css + app.js）
WEB_DIR = os.path.join(ROOT, "web")

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
async def predict(image: UploadFile = None, text: str = Form(""),
                 crop: str = Form("")):
    """图片+文本识别。image 留空则退化为纯文本特性检索。

    crop 可选标记：非空前端表示本次 image 为「框选区域裁剪图」
    （原为 /predict 兼容字段，仅用于回显识别模式，识别逻辑统一走 image）。
    """
    demo = get_demo()
    img = None
    if image is not None:
        data = await image.read()
        if not data:
            return {"error": "empty_image", "message": "上传的图片为空。"}
        img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    result = demo.predict_json(img, text)
    if crop:
        result = {**result, "cropped": True}
    return result


@app.post("/herb_sample_image")
async def herb_sample_image(payload: dict = Body(None)):
    """根据药材名（拼音目录名）从训练/验证集随机选一张图片，返回 base64。

    请求体：{"names": ["gouqizi", "rentian"]}
    返回：{"images": {"gouqizi": "data:image/jpeg;base64,....", ...}}（找不到的为 null）
    """
    import base64
    names = (payload or {}).get("names") or []
    demo = get_demo()
    result: dict = {}
    for name in names:
        path = demo.random_sample_image(name)
        if not path or not os.path.exists(path):
            result[name] = None
            continue
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            mime = "jpeg" if ext in ("jpg", "jpeg") else ext
            result[name] = f"data:image/{mime};base64,{b64}"
        except Exception:
            result[name] = None
    return {"images": result}


@app.post("/predict_multi")
async def predict_multi(images: list[UploadFile] = File(None),
                        texts: str = Form("")):
    """批量识别多个框选区域：一次请求收 N 张裁剪图，返回结果数组。

    用于「一张图里多种药材」场景：前端在整图上画出多个选区，逐个裁剪后
    打包上传，后端对每张图独立走现有分类器，再汇总成一个 zones 列表。

    请求（multipart/form-data）:
      images  多个裁剪后的选区图片（2 张以上）
      texts   JSON 数组字符串，与 images 一一对应的每区补充描述（可选）

    返回:
      zones    列表，每项形如 {"zone_idx", "top5", "kg_info", "similar",
                               "contraindications", "classic_formulas",
                               "formula", "confusable", "mode", "cropped",
                               "text"}
      compat   跨区配伍分析：复用知识图谱十八反/十九畏/相须相使
      disclaimer 医疗风险提示
    """
    demo = get_demo()
    if not images:
        return {"error": "empty_images", "message": "请至少上传一个选区图片。"}
    # 解析每区描述，与裁剪图按顺序一一对应
    zone_texts = []
    if texts:
        try:
            parsed = json.loads(texts)
            if isinstance(parsed, list):
                zone_texts = [str(t) for t in parsed]
        except Exception:
            zone_texts = []
    zones = []
    for idx, img_file in enumerate(images):
        data = await img_file.read()
        if not data:
            zones.append({"zone_idx": idx, "error": "empty_image",
                          "message": "选区图片为空。"})
            continue
        try:
            arr = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
        except Exception as e:
            zones.append({"zone_idx": idx, "error": "decode_failed",
                          "message": "图片解码失败：" + str(e)})
            continue
        zone_text = zone_texts[idx] if idx < len(zone_texts) else ""
        res = demo.predict_json(arr, zone_text)
        zones.append({**res, "zone_idx": idx, "cropped": True,
                      "text": zone_text})

    # 跨区配伍分析：取每个选区的 Top-1 药材名
    herb_names = [_strip_top1(z) for z in zones]
    compat = _build_compat_analysis(demo, herb_names)
    return {"zones": zones, "compat": compat, "disclaimer": _DISCLAIMER}


def _strip_top1(zone: dict) -> str:
    """从单个 zone 结果中取 Top-1 药材名（用于配伍分析）。"""
    top5 = zone.get("top5") or []
    return top5[0]["name"] if top5 else ""


def _build_compat_analysis(demo: HerbDemo, herb_names: list) -> dict:
    """基于知识图谱对多个药材做两两配伍分析（十八反/十九畏/相须相使）。

    返回 {"pairs": [...], "incompatible": [...], "restraint": [...], "paired": [...]}
    """
    out = {"pairs": [], "incompatible": [], "restraint": [], "paired": []}
    names = [n for n in herb_names if n]
    kg = getattr(demo, "kg", None)
    graph = getattr(kg, "graph", None) if kg else None
    if graph is None:
        return out
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if graph.has_edge(a, b):
                rel = graph[a][b].get("relation")
                if rel == "incompatible":
                    out["incompatible"].append([a, b])
                    out["pairs"].append({"a": a, "b": b, "relation": "incompatible"})
                elif rel == "restraint":
                    out["restraint"].append([a, b])
                    out["pairs"].append({"a": a, "b": b, "relation": "restraint"})
                elif rel == "paired":
                    out["paired"].append([a, b])
                    out["pairs"].append({"a": a, "b": b, "relation": "paired"})
    return out


@app.post("/search")
async def search(payload: dict):
    """纯文本特性检索：性味/归经/功效，返回所有符合的中草药（结构化）。"""
    demo = get_demo()
    text = (payload or {}).get("text", "")
    result = demo.kg.search_herbs_by_text(text)
    return {"query": text, "result": result,
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


@app.get("/graph")
def graph(focus: List[str] = Query([])):
    """药材关系图谱：返回力导向图 JSON（nodes + links + 分类配色）。

    query 参数:
      focus  聚焦药材名（可重复，如 ?focus=枸杞&focus=黄芪；也可在单个值内用
             逗号分隔，如 ?focus=枸杞,黄芪）；为空则返回全图（节点较多，前端自动适配）
    """
    demo = get_demo()
    # 兼容「单值逗号分隔」与「重复参数」两种写法，统一展开成列表
    focus_list = []
    for f in focus:
        for part in str(f).split(","):
            part = part.strip()
            if part:
                focus_list.append(part)
    focus_arg = focus_list if len(focus_list) > 1 else (focus_list[0] if focus_list else None)
    from app.graph_view import _CATEGORY_COLORS
    data = demo.kg.export_graph_json(focus=focus_arg)
    return {**data, "categoryColors": _CATEGORY_COLORS,
            "focus": focus_arg}


@app.get("/herbs")
def herbs():
    """返回全部药材名列表（供前端 datalist 自动补全）。"""
    demo = get_demo()
    return {"herbs": demo.kg.all_names()}


@app.post("/chat")
async def chat(images: list[UploadFile] = File(None),
               question: str = Form(""), history: str = Form("[]")):
    """外部 LLM 对话解释：本地识别 → 组装多轮上下文 → 调用 LLM 生成回答。

    请求（multipart/form-data）:
      question  必填，用户问题（如"这是什么药材？"）
      images    可选，一张或多张草药图片（同时识别，合并上下文并做跨图配伍分析）
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

    # 1) 接收图片：统一使用 images 字段（单图也是该数组，前端始终发送 images）
    img = None
    imgs = None
    if images:
        imgs = []
        for f in images:
            data = await f.read()
            if data:
                try:
                    imgs.append(np.array(Image.open(io.BytesIO(data)).convert("RGB")))
                except Exception:
                    continue
        if not imgs:
            imgs = None

    # 2) 构建本地识别上下文：多图逐张识别合并、单图识别、无图文本检索
    context, herbs, pred = demo.build_chat_context(question, img, imgs)
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
            "answer": f"{context}\n\n（注：未配置 DEEPSEEK_API_KEY，以上为本地知识图谱结果；"
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


# ---------------------------------------------------------------------------
# 收藏夹接口（后端 JSON 持久化）
# ---------------------------------------------------------------------------
from app.favorites_store import (  # noqa: E402
    load_favorites, add_herb, add_chat, remove_favorite, clear_favorites,
)


@app.get("/favorites")
def get_favorites(type: str = ""):
    """获取收藏列表；type=herb|chat 可过滤，默认返回全部。"""
    data = load_favorites()
    if type == "herb":
        return {"herbs": data["herbs"], "chats": []}
    if type == "chat":
        return {"herbs": [], "chats": data["chats"]}
    return data


@app.post("/favorites/herb")
async def post_favorite_herb(name: str = Form(""), info: str = Form("")):
    """收藏药材：name 必填，info 可选 JSON 字符串。"""
    parsed = {}
    if info and info.strip():
        try:
            parsed = json.loads(info)
        except (json.JSONDecodeError, ValueError):
            parsed = {}
    return add_herb(name, parsed)


@app.post("/favorites/chat")
async def post_favorite_chat(question: str = Form(""),
                             answer: str = Form(""),
                             rag_sources: str = Form(""),
                             image: str = Form("")):
    """收藏对话：question + answer。rag_sources 可选 JSON 字符串；image 可选 base64 data URL。"""
    sources = []
    if rag_sources and rag_sources.strip():
        try:
            sources = json.loads(rag_sources)
        except (json.JSONDecodeError, ValueError):
            sources = []
    img_b64 = image.strip() or None
    if img_b64 and not img_b64.startswith("data:image/"):
        img_b64 = None
    return add_chat(question, answer, sources, img_b64)


@app.delete("/favorites")
async def delete_favorite(fid: str = ""):
    """删除某条收藏（按 fid）。"""
    if not fid:
        return {"ok": False, "error": "empty_fid"}
    return remove_favorite(fid)


@app.delete("/favorites/clear")
async def clear_favorites_route(type: str = ""):
    """清空收藏；type=herb|chat 可选。"""
    return clear_favorites(type or None)


# 首页落地页（优先于 StaticFiles 兜底）：/ 指向 home.html，/app 指向原功能页 index.html
from fastapi.responses import FileResponse  # noqa: E402

@app.get("/", include_in_schema=False)
def home_page():
    """首页落地页：中草药多模态识别系统导引页。"""
    return FileResponse(os.path.join(WEB_DIR, "home.html"))

@app.get("/app", include_in_schema=False)
def workbench_page():
    """原功能工作台（5 大功能 tab 页）。"""
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# ---------------------------------------------------------------
# 导出对话为 PDF 文件（后端生成，直接下载，不调起浏览器打印）
# ---------------------------------------------------------------
@app.post("/api/export_chat_pdf")
def export_chat_pdf(messages: list = Body(...)):
    """将选中的对话（[{role, content}]）渲染为中文 PDF 文件返回。

    前端以 JSON 数组上传，后端用 fpdf2 + 系统中文字体（微软雅黑）生成，
    支持 markdown 基础排版（标题 / 粗体 / 列表 / 引用 / 代码块 / 表格）。
    """
    import tempfile
    from fpdf import FPDF

    # 选取系统中文字体（优先微软雅黑，缺失时回退到宋体/楷体）
    cn_font = None
    for cand in ("C:/Windows/Fonts/msyh.ttc",
                 "C:/Windows/Fonts/simhei.ttf",
                 "C:/Windows/Fonts/simsun.ttc",
                 "C:/Windows/Fonts/simkai.ttf"):
        if os.path.isfile(cand):
            cn_font = cand
            break

    class ChatPDF(FPDF):
        def header(self):
            pass

        def footer(self):
            self.set_y(-12)
            self.set_font("cn", size=8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 8, "本草识鉴 · 对话导出  -  第 %d 页" % self.page_no(),
                      align="C")

    pdf = ChatPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("cn", "", cn_font)
    pdf.add_font("cn", "B", cn_font)  # 粗体回退同一字体（视觉不加粗但不报错）
    pdf.set_margins(18, 16, 18)
    pdf.add_page()

    def role_label(role):
        return "用户" if role == "user" else "助手"

    for m in messages:
        role = m.get("role", "user") if isinstance(m, dict) else "user"
        content = (m.get("content", "") if isinstance(m, dict) else str(m)).strip()
        # 角色标签行
        pdf.set_font("cn", "B", 12)
        pdf.set_text_color(120, 50, 40)
        pdf.multi_cell(0, 7, role_label(role), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        # 正文（markdown 渲染）
        pdf.set_font("cn", "", 10.5)
        if content:
            # fpdf2 markdown 模式自动处理标题/粗体/列表/代码块等
            pdf.multi_cell(0, 5.6, content, markdown=True,
                           new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        # 分隔线
        y = pdf.get_y()
        pdf.set_draw_color(220, 220, 220)
        pdf.line(18, y, 192, y)
        pdf.ln(3)

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    pdf.output(path)

    from starlette.background import BackgroundTask
    def _cleanup(p=path):
        try:
            os.remove(p)
        except Exception:
            pass

    return FileResponse(path, media_type="application/pdf",
                        filename="本草对话导出.pdf",
                        background=BackgroundTask(_cleanup))

# 挂载静态前端（须在 API 路由定义之后，避免覆盖 /predict 等接口）
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", 8000))
    uvicorn.run(app, host="127.0.0.1", port=port)
