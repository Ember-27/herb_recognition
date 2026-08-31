"""Gradio 演示界面：上传图片 + 输入文本，返回识别结果与药性说明。"""
from typing import Dict, Any, Optional
import os
import numpy as np
import torch
from PIL import Image
import gradio as gr
import albumentations as A
from albumentations.pytorch import ToTensorV2

from models.classifier import build_classifier
from knowledge_graph.kg_builder import build_knowledge_graph, _normalize_name, _strip_pinyin
from utils.config import get_device
from utils.data_utils import build_label_maps
from app.llm_client import LLMClient, LLMError
from app.graph_view import build_graph_html
from app.rag_retriever import RAGRetriever
from app import ui_cards
from app.ui_theme import build_theme, GLOBAL_CSS, HEADER_HTML, DISCLAIMER_HTML


# ===== gradio_client 1.3.0 兼容补丁（Python<3.10 环境无法升级到 1.4+） =====
# gradio 4.44.1 的 /info API 文档路由在解析组件 schema 时，gradio_client 1.3.0
# 会对 bool 值执行 `in` 判断而抛 TypeError（argument of type 'bool' is not iterable）。
# 该路由由 gradio 内部无条件注册、无法用参数关闭，故在此对 schema 解析做防御性兜底，
# 保证即使 /info 被访问也不会返回 500，不影响主界面与所有交互功能。
def _patch_gradio_client_bool_bug() -> None:
    try:
        import gradio_client.utils as _gc_utils

        _orig_json_schema_to_python_type = _gc_utils.json_schema_to_python_type

        def _safe_json_schema_to_python_type(schema):
            try:
                if not isinstance(schema, dict):
                    return "Any"
                return _orig_json_schema_to_python_type(schema)
            except Exception:
                return "Any"

        _gc_utils.json_schema_to_python_type = _safe_json_schema_to_python_type

        _orig_value_is_file = _gc_utils.value_is_file

        def _safe_value_is_file(api_info):
            try:
                if not isinstance(api_info, dict):
                    return False
                return _orig_value_is_file(api_info)
            except Exception:
                return False

        _gc_utils.value_is_file = _safe_value_is_file
    except Exception:
        pass


_patch_gradio_client_bool_bug()


# 常见口语/饮片别名 -> 规范名（用于 AI 对话中自然语言药材名识别）
_HERB_ALIASES = {
    "枸杞子": "枸杞",
    "杭菊花": "菊花",
    "野菊花": "菊花",
    "北芪": "黄芪",
    "生甘草": "甘草",
    "炙甘草": "甘草",
    "太子参": "人参",
    "党参片": "党参",
    "当归片": "当归",
    "黄芪片": "黄芪",
}


_TRANSFORM = A.Compose([
    A.Resize(224, 224),
    A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
    ToTensorV2(),
])

# 中医药专家 system 提示词：与 app/api.py /chat 保持一致，要求 LLM 基于识别结果作答
_SYSTEM_PROMPT = (
    "你是一位资深的中医药专家，擅长中草药识别、药性解析与方剂配伍。\n"
    "回答规则：\n"
    "1. 严格基于用户提供的「本地识别结果」「知识库检索依据」与知识图谱信息作答，"
    "不要凭空编造药性、归经、功效或方剂；依据不足时如实说明。\n"
    "2. 明确区分信息来源：识别结果来自本地模型/知识图谱，"
    "其余补充知识需标注「参考中医典籍」；信息不足时直接说明「不确定」，"
    "并给出保守的鉴别建议。\n"
    "3. 用通俗中文回答，避免堆砌术语；涉及禁忌与用量时提示以医师诊断为准。\n"
    "4. 若回答涉及有毒药材（大毒/有毒/小毒/微毒）或任何用药建议，"
    "必须在结尾明确警告：切勿自行用药，须在执业中医师指导下使用。"
)


# 医疗风险提示与科普免责声明（安全红线：所有用户可见输出必须附带，见 plan.md 验收项 3）
_DISCLAIMER = (
    "⚠️ **医疗风险提示**：以上内容由本地识别模型与知识图谱自动生成，"
    "仅供**科普与学习参考**，不构成任何医疗诊断、处方或用药建议。"
    "中草药辨识与用药因人因证而异，请务必咨询**执业中医师或药师**，切勿自行用药。"
)


# 低置信度阈值：Top-1 置信度低于该值时强制提示人工复核（需求 1.5-2 / 创新项 IN10）
_CONFIDENCE_THRESHOLD = 0.5


def _low_confidence_hint(prob: float) -> str:
    """生成低置信度人工复核提示（Top-1 概率低于阈值时调用）。"""
    return (f"⚠️ **置信度提示**：最高识别置信度仅 {prob * 100:.1f}%"
            f"（低于 {_CONFIDENCE_THRESHOLD * 100:.0f}%），识别结果可信度不足。"
            f"建议补充更清晰的图片或药材特征描述后重试，并请专业人士人工复核确认，"
            f"请勿仅凭该结果用药。")


def _local_answer(pred: Dict) -> str:
    """无 LLM 时的降级回答：基于本地识别结果拼装自然语言（与 app/api.py 一致）。"""
    top5 = pred.get("top5") or []
    if not top5:
        return pred.get("message", "未识别到相关药材，请补充更明确的特性描述。")
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
    return "\n".join(lines)


class HerbDemo:
    def __init__(self, config: Dict[str, Any], ckpt_path: str = None,
                 model: torch.nn.Module = None, device: torch.device = None):
        self.config = config
        self.device = device or get_device(config["device"])
        if model is not None:
            self.model = model.to(self.device)
        else:
            # 类别数由训练集推导，确保与 label2idx 一致；模型必须显式搬到目标设备
            _, idx2label_tmp = build_label_maps(config["data"]["train_csv"])
            self.model = build_classifier(config, num_classes=len(idx2label_tmp)).to(self.device)
            if ckpt_path and os.path.exists(ckpt_path):
                self.model.load_state_dict(torch.load(ckpt_path, map_location=self.device))
                print(f"[INFO] 已加载权重: {ckpt_path}")
            else:
                print("[WARN] 未找到权重，使用随机初始化模型演示 (请先训练)。")
        self.model.eval()
        self.kg = build_knowledge_graph(config)
        _, self.idx2label = build_label_maps(config["data"]["train_csv"])
        self.retriever = None  # RAG 检索器（首次对话时懒加载）
        # 药材样本图缓存：name -> 一张随机选取并保留的图片（展示用）
        self._sample_cache: Dict[str, str] = {}
        # 中文名 -> 拼音目录名 映射（样本图目录按拼音名组织，而 CSV 的 label 是中文名）
        self._zh2py: Dict[str, str] = {}
        train_csv = config.get("data", {}).get("train_csv")
        if train_csv and os.path.exists(train_csv):
            try:
                import csv as _csv
                with open(train_csv, "r", encoding="utf-8", newline="") as _f:
                    _reader = _csv.DictReader(_f)
                    for _row in _reader:
                        _zh = (_row.get("label") or "").strip()
                        _zh_stripped = _strip_pinyin(_zh)
                        _path = _row.get("image_path") or ""
                        if not _zh_stripped or _zh_stripped in self._zh2py:
                            continue
                        # image_path 形如 .../cls_chinese_medicine/train/aiye/aiye_0001.jpg -> 拼音目录名
                        _parts = [p for p in _path.replace("\\", "/").split("/") if p]
                        if len(_parts) >= 2:
                            self._zh2py[_zh_stripped] = _parts[-2]
                        if len(self._zh2py) >= (config.get("data", {}).get("num_classes") or 100000):
                            break
            except Exception as _e:
                print(f"[WARN] 构建中文名->拼音目录映射失败: {_e}")
        self._sample_dirs = []
        # 优先使用预生成的药材图鉴目录（images/图鉴），稳定且无需实时扫描大数据集
        atlas_root = config.get("data", {}).get("herb_atlas_dir")
        if not atlas_root:
            atlas_root = os.path.normpath(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "images", "图鉴"))
        if os.path.isdir(atlas_root):
            self._sample_dirs = [atlas_root]
        else:
            print(f"[WARN] 未找到图鉴目录 {atlas_root}，请运行 tools/build_herb_atlas.py 生成。")

        # 兜底图源：相似药推荐中「不在训练集」的药材图片（images/addition/），
        # 由 tools/fetch_addition_herbs.py 生成，herb_map.json 提供 中文名->拼音文件名 映射。
        # 查找顺序：图鉴目录优先，addition 兜底；addition 内部用 herb_map 解析中文名。
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._addition_dir = os.path.normpath(os.path.join(proj_root, "images", "addition"))
        self._herb_map: Dict[str, str] = {}  # 中文名 -> 拼音文件名(不含扩展名)
        map_file = os.path.join(self._addition_dir, "herb_map.json")
        if os.path.isfile(map_file):
            try:
                import json as _json
                with open(map_file, "r", encoding="utf-8") as _f:
                    _raw = _json.load(_f)
                for _zh, _fn in _raw.items():
                    self._herb_map[_zh] = os.path.splitext(_fn)[0]
            except Exception as _e:
                print(f"[WARN] 读取 addition/herb_map.json 失败: {_e}")
        elif os.path.isdir(self._addition_dir):
            print(f"[WARN] 未找到 {map_file}，addition 目录将退回按拼音名直接匹配。")

    def predict(self, image, text: str):
        # 纯文本识别：无图片时按特性检索匹配最可能的药材（完全匹配优先，Top-5）
        if image is None:
            if not text or not text.strip():
                return ("请上传图片，或输入文本描述（如：味甘平，归肝肾经，滋补肝肾）。\n\n"
                        + _DISCLAIMER), _DISCLAIMER
            res = self.kg.search_herbs_by_text(text)
            cands = (res["full"] + res["partial"])[:5]
            if not cands:
                return ("未匹配到任何药材，请补充更明确的性味/归经/功效关键词。\n\n"
                        + _DISCLAIMER), _DISCLAIMER
            lines = ["（纯文本匹配 Top-5，图片可选）"]
            for i, item in enumerate(cands, 1):
                lines.append(self._format_match_item(i, item, text))
            # 以匹配度最高者为代表，给出完整药性说明、相似药推荐与方剂推荐
            top_name = cands[0]["name"]
            kg_parts = [self.kg.describe(top_name)]
            conf_txt = self._format_confusable(top_name)
            if conf_txt:
                kg_parts.append(conf_txt)
            fml_txt = self._format_classic_formulas(top_name)
            if fml_txt:
                kg_parts.append(fml_txt)
            risk_txt = self._format_contra_risk(top_name)
            if risk_txt:
                kg_parts.append(risk_txt)
            sim_txt = self._similar_herbs_text(top_name)
            if sim_txt:
                kg_parts.append(sim_txt)
            formula = self.kg.recommend_formula(top_name, symptoms=text, top_k=4)
            if formula:
                fr = "\n".join([f"{j+1}. {r['herb']}  (依据: {r['reason']})"
                                for j, r in enumerate(formula)])
            else:
                fr = "暂无可推荐的配伍（或均存在禁忌）。"
            kg_parts.append(f"【方剂推荐（以 {top_name} 为君药）】\n{fr}")
            return ("\n\n".join(lines) + "\n\n" + _DISCLAIMER,
                    "\n\n".join(kg_parts) + "\n\n" + _DISCLAIMER)

        img = Image.fromarray(image).convert("RGB")
        tensor = _TRANSFORM(image=np.array(img))["image"].unsqueeze(0)
        texts = [text] if text else [""]
        with torch.no_grad():
            # 推理自动分支：有文本走多模态，无文本走纯视觉保底
            logits = self.model.predict(tensor, texts, device=self.device)
            probs = torch.softmax(logits, dim=1)[0]
            topk = torch.topk(probs, min(3, len(probs)))
        preds = [(_strip_pinyin(self.idx2label[int(i)]), float(p))
                 for i, p in zip(topk.indices, topk.values)]
        result = "\n".join([f"{name}: {prob*100:.1f}%" for name, prob in preds])
        # 低置信度人工复核引导（需求 1.5-2 / 创新项 IN10）
        if preds and preds[0][1] < _CONFIDENCE_THRESHOLD:
            result += f"\n\n{_low_confidence_hint(preds[0][1])}"
        # 用最高置信度结果查知识图谱
        top_name = preds[0][0]
        kg_desc = self.kg.describe(top_name)
        # 相似药材鉴别：识别到易混淆对时给出外观差异与简易鉴别法
        conf_txt = self._format_confusable(top_name)
        if conf_txt:
            kg_desc = kg_desc + f"\n\n{conf_txt}"
        # 经典方剂参考：含该药材的方剂（组成/主治/用法/风险）
        fml_txt = self._format_classic_formulas(top_name)
        if fml_txt:
            kg_desc = kg_desc + f"\n\n{fml_txt}"
        # 配伍风险显式标注（十八反/十九畏自动规避提示）
        risk_txt = self._format_contra_risk(top_name)
        if risk_txt:
            kg_desc = kg_desc + f"\n\n{risk_txt}"
        # 相似药推荐：功效分类相近的其它药材
        sim_txt = self._similar_herbs_text(top_name)
        if sim_txt:
            kg_desc = kg_desc + f"\n\n{sim_txt}"
        # 方剂推荐：以 Top-1 为主药，结合可选文本做症状匹配
        formula = self.kg.recommend_formula(top_name, symptoms=text)
        if formula:
            fr = "\n".join([f"{i+1}. {r['herb']}  (依据: {r['reason']})"
                            for i, r in enumerate(formula)])
        else:
            fr = "暂无可推荐的配伍（或均存在禁忌）。"
        kg_desc = kg_desc + f"\n\n【方剂推荐（以 {top_name} 为君药）】\n{fr}"
        return result, kg_desc

    def random_sample_image(self, name: str) -> Optional[str]:
        """为某药材取一张样本图，查找顺序：图鉴目录(images/图鉴) -> addition 兜底(images/addition)。

        图鉴/拼音目录名如 gouqizi.jpg；addition 内部用 herb_map.json 的 中文名->拼音文件名 映射。
        name 可为中文名或拼音名，解析后查找对应文件；找不到返回 None。结果按拼音名缓存。
        """
        raw = (name or "").strip()
        if not raw:
            return None
        # 解析为拼音目录名：先用中文名->拼音映射，再尝试原样（已是拼音目录名）
        py = self._zh2py.get(raw) or self._zh2py.get(_strip_pinyin(raw)) or raw
        if py in self._sample_cache:
            return self._sample_cache[py]
        found = None
        # 1) 优先在图鉴目录(训练集类)查找
        for base in self._sample_dirs:
            for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                cand = os.path.join(base, py + ext)
                if os.path.isfile(cand):
                    found = cand
                    break
            if found:
                break
        # 2) 兜底：addition 目录（相似药推荐中不在训练集的药材）
        if not found and os.path.isdir(self._addition_dir):
            add_py = self._herb_map.get(raw) or self._herb_map.get(_strip_pinyin(raw)) or py
            for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                cand = os.path.join(self._addition_dir, add_py + ext)
                if os.path.isfile(cand):
                    found = cand
                    break
        self._sample_cache[py] = found  # 缓存（含 None，避免重复扫描）
        return found

    def _sample_image_datauri(self, name: str) -> Optional[str]:
        """取药材样本图并转为 base64 data URI（供 Gradio gr.HTML 内联展示）。

        Gradio 4.x 的 gr.HTML 无法访问本地绝对路径文件，故读图后以 data URI 内联，
        避免额外暴露静态目录。找不到图返回 None（卡片自动降级为无图）。
        """
        path = self.random_sample_image(name)
        if not path or not os.path.isfile(path):
            return None
        try:
            from io import BytesIO
            import base64
            img = Image.open(path).convert("RGB")
            # 限制尺寸，控制 HTML 体积（最长边 256px）
            max_side = 256
            if max(img.size) > max_side:
                ratio = max_side / max(img.size)
                img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as _e:
            print(f"[WARN] 样本图转 data URI 失败 {name}: {_e}")
            return None

    def predict_json(self, image, text: str = "") -> dict:
        """结构化识别接口（REST API 与 Gradio 共用），返回 JSON 友好的 dict。

        image 为 numpy 数组（RGB）或 None；text 为可选文本描述。
        无图片时按特性检索返回 Top-5；有图片时返回 Top-5 + 知识图谱说明 + 方剂推荐。
        """
        # 纯文本特性检索 Top-5
        if image is None:
            if not text or not text.strip():
                return {"error": "no_input",
                        "message": "请上传图片或输入文本描述。",
                        "disclaimer": _DISCLAIMER}
            res = self.kg.search_herbs_by_text(text)
            cands = (res["full"] + res["partial"])[:5]
            top_name = cands[0]["name"] if cands else None
            return {
                "mode": "text_search",
                "query": text,
                "top5": [{
                    "name": _strip_pinyin(it["name"]),
                    "score": it["score"],
                    "dims": {k: bool(v) for k, v in it["dims"].items()},
                    "hits": {k: list(v) for k, v in it["hits"].items()},
                    "toxicity": (it["info"] or {}).get("toxicity", "无毒"),
                    "info": it["info"],
                } for it in cands],
                "kg_info": self.kg.describe(top_name) if top_name else None,
                "similar": self._similar_herbs(top_name) if top_name else [],
                "confusable": self.kg.confusable_of(top_name) if top_name else None,
                "classic_formulas": self.kg.classic_formulas(top_name)
                if top_name else [],
                "contraindications": self.kg.contraindications(top_name)
                if top_name else {"incompatible": [], "restraint": []},
                "formula": [],
                "disclaimer": _DISCLAIMER,
            }

        img = Image.fromarray(image).convert("RGB")
        tensor = _TRANSFORM(image=np.array(img))["image"].unsqueeze(0)
        texts = [text] if text else [""]
        with torch.no_grad():
            logits = self.model.predict(tensor, texts, device=self.device)
            probs = torch.softmax(logits, dim=1)[0]
            topk = torch.topk(probs, min(5, len(probs)))
        preds = [{
            "name": _strip_pinyin(self.idx2label[int(i)]), "prob": float(p),
            "toxicity": (self.kg.get_info(_strip_pinyin(self.idx2label[int(i)])) or {})
                        .get("toxicity", "无毒"),
        } for i, p in zip(topk.indices, topk.values)]
        top_name = preds[0]["name"]
        formula = self.kg.recommend_formula(top_name, symptoms=text)
        return {
            "mode": "image",
            "top5": preds,
            "low_confidence": preds[0]["prob"] < _CONFIDENCE_THRESHOLD,
            "kg_info": self.kg.describe(top_name),
            "similar": self._similar_herbs(top_name),
            "confusable": self.kg.confusable_of(top_name),
            "classic_formulas": self.kg.classic_formulas(top_name),
            "contraindications": self.kg.contraindications(top_name),
            "formula": [{"herb": r["herb"], "reason": r["reason"],
                         "toxicity": r.get("toxicity", "无毒")}
                        for r in formula] if formula else [],
            "disclaimer": _DISCLAIMER,
        }

    def predict_html(self, image, text: str = "") -> tuple:
        """图片识别页签：结构化识别 + 渲染为宣纸质感卡片 HTML（新中式 UI）。"""
        pred = self.predict_json(image, text)
        if pred.get("error"):
            return (ui_cards.note_card(
                pred.get("message", "请上传图片或输入文本描述。")), DISCLAIMER_HTML)
        top5 = pred.get("top5") or []
        if top5:
            pred["_info"] = self.kg.get_info(top5[0].get("name")) or {}
        # 注入药材样本图（base64 data URI）：候选 Top-5 + 相似药推荐，供卡片内联展示
        for it in top5:
            it["image_b64"] = self._sample_image_datauri(it.get("name"))
        for it in (pred.get("similar") or []):
            it["image_b64"] = self._sample_image_datauri(it.get("name"))
        return ui_cards.render_predict_cards(pred), DISCLAIMER_HTML

    def search_text_html(self, text: str) -> str:
        """特性检索页签：检索结果渲染为卡片网格（新中式 UI）。"""
        if not text or not text.strip():
            return ui_cards.note_card(
                "请输入特性描述，例如：味甘平，归肝肾经，滋补肝肾、益精明目。\n\n"
                "支持条件：\n"
                "- 性味：甘/苦/辛/酸/咸/淡/涩 + 寒/热/温/凉/平（如 甘平、甘微寒）\n"
                "- 归经：肝/心/脾/肺/肾/胃/胆/大肠/小肠/膀胱/三焦/心包（如 归肝肾经）\n"
                "- 功效：滋补/清热/活血/安神/化痰等（如 滋补肝肾、清热明目）",
                "使用说明")
        result = self.kg.search_herbs_by_text(text)
        return ui_cards.render_search_cards(result)

    def _similar_herbs(self, name: str, top_k: int = 5) -> list:
        """相似药推荐：与 name 功效分类相近的其它药材（结构化，供 JSON 接口）。"""
        return [{
            "name": s,
            "categories": (self.kg.get_info(s) or {}).get("categories", []),
        } for s in self.kg.similar_by_function(name, top_k=top_k)]

    def _similar_herbs_text(self, name: str, top_k: int = 5) -> str:
        """相似药推荐（可读文本）：功效相近的其它药材及分类说明。"""
        sim = self._similar_herbs(name, top_k)
        if not sim:
            return ""
        items = [f"{s['name']}（{'、'.join(s['categories'])}）"
                 if s["categories"] else s["name"] for s in sim]
        return "【相似药推荐（功效相近）】\n" + "；".join(items)

    def _format_confusable(self, name: str) -> str:
        """易混淆药材外观鉴别（可读文本）：识别到易混淆对时给出鉴别要点。"""
        conf = self.kg.confusable_of(name)
        if not conf:
            return ""
        peer = conf["peer"]
        lines = [
            f"🔍 **相似药材鉴别**：{name} 与 **{peer}**（{conf.get('category', '外形相似')}）易混淆",
            f"- 易混原因：{conf.get('why_confused', '')}",
            f"- 外观差异：{conf.get('appearance_diff', '')}",
            f"- 简易鉴别：{conf.get('simple_test', '')}",
        ]
        if conf.get("note"):
            lines.append(f"- 使用提示：{conf['note']}")
        return "\n".join(lines)

    def _format_contra_risk(self, name: str) -> str:
        """配伍风险显式标注：明确提示哪些药因十八反/十九畏被自动规避。"""
        contra = self.kg.contraindications(name)
        parts = []
        if contra.get("incompatible"):
            parts.append(f"与{'、'.join(contra['incompatible'])}存在**十八反**")
        if contra.get("restraint"):
            parts.append(f"与{'、'.join(contra['restraint'])}存在**十九畏**")
        if not parts:
            return ""
        return ("⚠️ **配伍风险提示**：方剂推荐已自动规避——" + "；".join(parts)
                + "。含禁忌配伍的组方不可使用，务必经执业医师确认。")

    def _format_classic_formulas(self, herb: str) -> str:
        """经典方剂推荐（可读文本）：含方剂名、组成、主治、用法与风险提示。"""
        formulas = self.kg.classic_formulas(herb, top_k=3)
        if not formulas:
            return ""
        blocks = ["📜 **经典方剂参考**（含该药的方剂）"]
        for f in formulas:
            lines = [f"- **{f['name']}**（{f['source']}）"]
            if f.get("category"):
                lines.append(f"  - 分类：{f['category']}")
            lines.append(f"  - 组成：{f['composition_text']}")
            if f.get("effects"):
                lines.append(f"  - 功效：{f['effects']}")
            if f.get("indications"):
                lines.append(f"  - 主治：{f['indications']}")
            if f.get("usage"):
                lines.append(f"  - 用法：{f['usage']}")
            if f.get("warning"):
                lines.append(f"  - ⚠️ 禁忌/注意：{f['warning']}")
            blocks.append("\n".join(lines))
        return "\n".join(blocks)

    def search_text(self, text: str) -> str:
        """特性检索：返回所有符合所给性味/归经/功效特性的中草药。"""
        if not text or not text.strip():
            return ("请输入特性描述，例如：**味甘平，归肝肾经，滋补肝肾、益精明目**\n\n"
                    "支持条件：\n"
                    "- 性味：甘/苦/辛/酸/咸/淡/涩 + 寒/热/温/凉/平（如 甘平、甘微寒）\n"
                    "- 归经：肝/心/脾/肺/肾/胃/胆/大肠/小肠/膀胱/三焦/心包（如 归肝肾经）\n"
                    "- 功效：滋补/清热/活血/安神/化痰等（如 滋补肝肾、清热明目）")
        result = self.kg.search_herbs_by_text(text)
        parsed = result["parsed"]
        total = result["total_conditions"]
        if total == 0:
            return ("未解析出有效条件，请按「性味 + 归经 + 功效」描述，"
                    "例如：味甘平，归肝肾经，滋补肝肾。")
        cond_desc = []
        fn = parsed["flavor"] + parsed["nature"]
        if fn:
            cond_desc.append(f"性味「{'、'.join(fn)}」")
        if parsed["meridian"]:
            cond_desc.append(f"归经「{'、'.join(parsed['meridian'])}」")
        func_cands = list(dict.fromkeys(parsed["function_kws"] + parsed["function_segs"]))
        if func_cands:
            cond_desc.append(f"功效「{'、'.join(func_cands[:8])}」")
        parts = [f"**解析条件**：{'，'.join(cond_desc)}（共 {total} 类）"]
        full, partial = result["full"], result["partial"]
        parts.append(f"## 完全匹配 {total}/{total}（共 {len(full)} 种）")
        if full:
            for i, item in enumerate(full, 1):
                parts.append(self._format_match_item(i, item, text))
        else:
            parts.append("_无_")
        parts.append(f"## 部分匹配（命中 ≥1 且 <{total}，共 {len(partial)} 种）")
        if partial:
            for i, item in enumerate(partial, 1):
                parts.append(self._format_match_item(i, item, text))
        else:
            parts.append("_无_")
        if result.get("hint"):
            parts.append(f"> {result['hint']}")
        # 匹配度最高者的易混淆外观鉴别与经典方剂（有则提示）
        if result.get("full"):
            conf_txt = self._format_confusable(result["full"][0]["name"])
            if conf_txt:
                parts.append(conf_txt)
            fml_txt = self._format_classic_formulas(result["full"][0]["name"])
            if fml_txt:
                parts.append(fml_txt)
        parts.append(f"---\n\n{_DISCLAIMER}")
        return "\n\n".join(parts)

    def _extract_herbs_from_question(self, question: str):
        """从自然语言问题中提取知识图谱里的药材名（支持常见别名）。"""
        q = question
        for alias, std in _HERB_ALIASES.items():
            q = q.replace(alias, std)
        names = sorted(self.kg.all_names(), key=len, reverse=True)
        found = []
        used = set()
        for n in names:
            if n not in used and n in q:
                found.append(_normalize_name(n))
                used.add(n)
                q = q.replace(n, " ")
        return found

    def _build_context_from_herbs(self, herbs, question: str) -> str:
        """基于提取的药材名构造知识图谱上下文（含配伍分析）。"""
        lines = ["【识别到的药材】"]
        for h in herbs:
            lines.append(self.kg.describe(h))

        if len(herbs) >= 2:
            lines.append("\n【配伍分析】")
            for i in range(len(herbs)):
                for j in range(i + 1, len(herbs)):
                    a, b = herbs[i], herbs[j]
                    if self.kg.graph.has_edge(a, b):
                        rel = self.kg.graph[a][b].get("relation")
                        if rel == "incompatible":
                            lines.append(f"⚠️ {a} 与 {b} 存在十八反配伍禁忌，不建议同用。")
                        elif rel == "restraint":
                            lines.append(f"⚠️ {a} 与 {b} 存在十九畏配伍顾忌，需谨慎同用。")
                        elif rel == "paired":
                            lines.append(f"✅ {a} 与 {b} 为常用相须相使配伍，可以一起使用。")
                    else:
                        info_a = self.kg.get_info(a)
                        info_b = self.kg.get_info(b)
                        cats_a = set(info_a["categories"]) if info_a else set()
                        cats_b = set(info_b["categories"]) if info_b else set()
                        common = cats_a & cats_b
                        if common:
                            lines.append(f"ℹ️ {a} 与 {b} 功效分类同属 {'、'.join(common)}，可配伍使用。")
                        else:
                            lines.append(f"ℹ️ {a} 与 {b} 在知识图谱中无明确配伍禁忌或推荐记录，常规可同用。")
        elif len(herbs) == 1:
            lines.append("\n【相似药/方剂推荐】")
            formula = self.kg.recommend_formula(herbs[0], symptoms=question, top_k=4)
            if formula:
                lines.append("；".join([f"{r['herb']}（{r['reason']}）" for r in formula]))
            else:
                lines.append("暂无可推荐的配伍（或均存在禁忌）。")
        return "\n\n".join(lines)

    def build_chat_context(self, question: str, image=None):
        """构造 AI 对话的本地上下文。

        支持图文混合：优先从问题中提取药材名并查询知识图谱；
        若同时提供图片（image），以图片识别结果作为上下文主体，文本问题作为补充。
        提取不到药材名时回退为特性检索。返回 (context, herbs, pred_dict)。
        """
        # 图文混合问答：图片优先做本地识别，识别结果作为对话上下文
        if image is not None:
            pred = self.predict_json(image, question)
            context = _local_answer(pred)
            herbs = []
            if pred.get("mode") == "image":
                herbs = [it["name"] for it in (pred.get("top5") or [])][:3]
            return context, herbs, pred
        herbs = self._extract_herbs_from_question(question)
        if herbs:
            context = self._build_context_from_herbs(herbs, question)
            pred = {
                "mode": "herb_mentions",
                "query": question,
                "herbs": herbs,
                "top5": [{"name": h, "score": "-", "info": self.kg.get_info(h)} for h in herbs],
                "kg_info": context,
            }
        else:
            pred = self.predict_json(None, question)
            context = _local_answer(pred)
        return context, herbs, pred

    def _llm_chat(self, question: str, history: list = None, image=None) -> tuple:
        """网页端 AI 对话：本地识别上下文 + LLM 生成（与 FastAPI /chat 相同的降级逻辑）。

        支持图文混合问答：上传图片（image）+ 文本问题 → 先本地识别图片得到药材上下文，
        再结合文本问题回答；无图时按问题做特性检索 Top-5 作为上下文。
        未配置 Key 或调用失败时，自动降级返回本地知识图谱结果。
        返回 (新对话历史, 清空后的输入框)。
        """
        history = list(history) if history else []
        question = (question or "").strip()
        if not question and image is None:
            return history, ""
        # 1) 本地识别上下文：优先从自然语言问题中提取药材名，再回退特性检索；有图时走图片识别
        context, herbs, pred = self.build_chat_context(question, image)
        # 2) RAG 知识库检索依据（首次调用懒加载本地 BERT，检索失败不影响主流程）
        rag_text, rag_sources = "", []
        try:
            if self.retriever is None:
                self.retriever = RAGRetriever(self.kg)
            rag_text, rag_sources = self.retriever.retrieve(question, herbs)
        except Exception as e:
            print(f"[RAG] 检索失败（继续对话）: {e}")
        # 3) 组装 messages：system + 最近 10 轮历史 + 当前问题（附本地识别结果 + RAG 依据）
        user_content = f"【本地识别结果】\n{context}\n\n【用户问题】\n{question}"
        if rag_text:
            user_content = (f"【本地识别结果】\n{context}\n\n"
                            f"【知识库检索依据（可引用，勿编造依据外的信息）】\n{rag_text}\n\n"
                            f"【用户问题】\n{question}")
        msgs = [{"role": "system", "content": _SYSTEM_PROMPT}]
        msgs += [{"role": m["role"], "content": m["content"]}
                 for m in history
                 if m.get("role") in ("user", "assistant")
                 and isinstance(m.get("content"), str) and m["content"].strip()][-20:]
        msgs.append({"role": "user", "content": user_content})
        new_history = history + [{"role": "user", "content": question}]
        # 4) 调用 LLM；未配置或失败时降级为本地知识图谱结果
        llm = LLMClient()
        if not llm.available:
            answer = (f"{context}\n\n（注：未配置 DEEPSEEK_API_KEY，以上为本地知识图谱结果；"
                      f"配置后可获得更自然的对话解释。）")
        else:
            try:
                answer = llm.chat(msgs)
            except LLMError as e:
                answer = (f"{context}\n\n（注：LLM 调用失败：{e}。"
                          f"以上为本地知识图谱结果。）")
        # 5) 证据链：回答末尾固定标注本次引用的知识库条目
        if rag_sources:
            src = "、".join(s["title"] for s in rag_sources[:4])
            answer = f"{answer}\n\n📚 知识库依据：{src}"
        # 6) 医疗风险提示（安全红线）：所有对话回答统一附带免责声明
        answer = f"{answer}\n\n{_DISCLAIMER}"
        return new_history + [{"role": "assistant", "content": answer}], ""

    def _format_match_item(self, idx: int, item: Dict, query_text: str) -> str:
        """格式化单味检索结果：命中/缺失条件标注 + 方剂推荐（完全匹配）。"""
        dims, hits, info = item["dims"], item["hits"], item["info"]
        marks = []
        if dims["flavor"]:
            marks.append(f"性味✓({('、'.join(hits['flavor'])) or '全部'})")
        else:
            marks.append("性味✗")
        if dims["meridian"]:
            marks.append(f"归经✓({('、'.join(hits['meridian'])) or '全部'})")
        else:
            marks.append("归经✗")
        if dims["function"]:
            marks.append(f"功效✓({('、'.join(hits['function'][:5])) or '命中'})")
        else:
            marks.append("功效✗")
        lines = [
            f"{idx}. **{item['name']}**　匹配度 {item['score']} 分　|　{'　'.join(marks)}",
            f"   - 药性：{info['property']} ｜ 归经：{info['meridian']}"
            f" ｜ 毒性：{info.get('toxicity', '无毒')}",
            f"   - 功效：{info['function']}",
        ]
        if info.get("aliases"):
            lines.append(f"   - 别名：{'、'.join(info['aliases'])}")
        if info.get("indications"):
            lines.append(f"   - 适用病症：{info['indications']}")
        if info.get("cautions"):
            lines.append(f"   - ⚠️ 个体禁忌：{info['cautions']}")
        # 毒性强制警示（安全红线）：大毒/有毒醒目提示，小毒/微毒提醒限量
        tox = info.get("toxicity", "无毒")
        if tox in ("大毒", "有毒"):
            lines.append(f"   - ⚠️ **毒性警示**：{item['name']}为{tox}药材，"
                         f"严禁自行煎服或超量使用，须在执业中医师指导下辨证用药！")
        elif tox in ("小毒", "微毒"):
            lines.append(f"   - ⚠️ 注意：{item['name']}含{tox}成分，用量需谨慎，请遵医嘱使用。")
        if all(dims.values()):
            formula = self.kg.recommend_formula(item["name"], symptoms=query_text, top_k=4)
            if formula:
                fr = "；".join([f"{r['herb']}（{r['reason']}）" for r in formula])
                lines.append(f"   - 方剂推荐：{fr}")
            else:
                lines.append("   - 方剂推荐：暂无可推荐的配伍（或均存在禁忌）")
            risk_txt = self._format_contra_risk(item["name"])
            if risk_txt:
                lines.append(f"   - {risk_txt}")
        return "\n".join(lines)

    def explain(self, image, text: str = ""):
        """Grad-CAM 可视化：展示模型分类时重点关注的图像区域。

        返回 (叠加热图 numpy uint8, 说明文字)。
        兼容 Swin / ResNet / EfficientNet / ConvNeXt 主干：
          - Transformer 类主干 hook 最后一个 stage 的 token 特征 [B, N, C]
          - CNN 类主干 hook 最后一个卷积块的特征图 [B, C, H, W]
        """
        if image is None:
            return None, "请先上传图片，才能生成关注区域热图。"
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.cm as mcm
        from torch.nn import functional as F

        img = Image.fromarray(image).convert("RGB")
        tensor = _TRANSFORM(image=np.array(img))["image"].unsqueeze(0)
        model = self.model
        has_text = bool(text and text.strip())

        # 定位可 hook 的特征层
        bb = model.vision.backbone
        if hasattr(bb, "layers"):        # swin / convnext
            layer = bb.layers[-1]
        elif hasattr(bb, "layer4"):      # resnet
            layer = bb.layer4
        elif hasattr(bb, "blocks"):      # efficientnet
            layer = bb.blocks[-1]
        else:
            return None, f"暂不支持主干类型: {type(bb).__name__}"

        feats = {}

        def fwd_hook(m, i, o):
            feats["x"] = o.detach()

        def bwd_hook(m, gi, go):
            feats["g"] = go[0].detach()

        h1 = layer.register_forward_hook(fwd_hook)
        h2 = layer.register_full_backward_hook(bwd_hook)
        try:
            x = tensor.to(self.device).requires_grad_(True)
            texts = [text] if has_text else [""]
            with torch.set_grad_enabled(True):
                v = model.vision(x)
                if has_text:
                    t = model.text(texts, device=self.device)
                    logits = model.head(model.fusion(v, t))
                else:
                    logits = model.vision_head(v)
                probs = torch.softmax(logits, dim=1)[0]
                pred = int(probs.argmax())
                model.zero_grad()
                logits[0, pred].backward()
        finally:
            h1.remove()
            h2.remove()
            model.zero_grad()

        out = feats["x"]          # [B,H,W,C]（Swin）或 [B,N,C] 或 [B,C,H,W]（CNN）
        grad = feats.get("g")     # 与 out 同形
        if out.dim() == 4:
            if out.shape[3] >= out.shape[1] and out.shape[3] >= out.shape[2]:
                # 通道在后 [B,H,W,C]（Swin/ConvNeXt）：对梯度做空间 GAP 得到类别权重
                wgt = grad[0].mean(dim=(0, 1)) if grad is not None else None
                if wgt is None:
                    wgt = out[0].mean(dim=(0, 1))          # 主干冻结时退化为均值权重
                cam = torch.relu((out[0] * wgt).sum(dim=2))  # [H, W]
                side = out.shape[1]
            else:
                # 通道在前 [B,C,H,W]（ResNet/EfficientNet）
                wgt = grad[0].mean(dim=(1, 2)) if grad is not None else None
                if wgt is None:
                    wgt = out[0].mean(dim=(1, 2))
                cam = torch.relu((out[0] * wgt.unsqueeze(1).unsqueeze(2)).sum(dim=0))  # [H, W]
                side = out.shape[2]
        else:                   # Transformer token: [B, N, C]
            fm = out[0]
            if grad is not None:
                weights = grad[0].mean(dim=0, keepdim=True)     # [1, C]
                cam = torch.relu((weights * fm).sum(dim=1))     # [N]
            else:
                # 主干被冻结时无梯度，退化为"权重 CAM"（proj 后与分类头对齐）
                w = model.vision_head.weight[pred].detach()
                cam = torch.relu(model.vision.proj(fm) @ w)     # [N]
            side = int(round(fm.shape[0] ** 0.5))

        cam = cam.reshape(1, 1, side, side)
        cam = F.interpolate(cam, size=(224, 224), mode="bilinear", align_corners=False)
        cam = cam.squeeze().cpu().numpy()
        cmin, cmax = cam.min(), cam.max()
        cam = (cam - cmin) / (cmax - cmin + 1e-8)

        try:
            cmap_jet = mcm.get_cmap("jet")          # matplotlib < 3.9
        except AttributeError:
            cmap_jet = matplotlib.colormaps["jet"]  # matplotlib >= 3.9
        heat = cmap_jet(cam)[:, :, :3]              # [224,224,3] 0-1
        base = np.array(img.resize((224, 224)), dtype=np.float32) / 255.0
        overlay = (base * 0.55 + heat * 0.45)
        overlay = (overlay * 255).astype(np.uint8)

        label = self.idx2label[pred]
        info = (f"**Grad-CAM 关注区域**（Top-1: **{label}**，置信度 {probs[pred] * 100:.1f}%"
                f"，{'多模态' if has_text else '纯视觉'}模式）\n\n"
                f"红/黄色区域是模型判定为「{label}」时主要依据的图像部位。"
                f"若高亮集中在叶片/花朵等药材本体，说明模型学到了真实形态特征。")
        # 毒性强制警示（安全红线）：识别结果为有毒药材时醒目提示
        tox = (self.kg.get_info(label) or {}).get("toxicity", "无毒")
        if tox in ("大毒", "有毒"):
            info += (f"\n\n⚠️ **毒性警示**：识别结果为 **{label}**（{tox}药材），"
                     f"仅供识别演示，严禁据此自行煎服或用药，须在执业中医师指导下使用！")
        elif tox in ("小毒", "微毒"):
            info += (f"\n\n⚠️ 注意：识别结果为 **{label}**（含{tox}成分），"
                     f"用量需谨慎，请在医师指导下使用。")
        # 低置信度人工复核引导（需求 1.5-2 / 创新项 IN10）
        if float(probs[pred]) < _CONFIDENCE_THRESHOLD:
            info += f"\n\n{_low_confidence_hint(float(probs[pred]))}"
        info += f"\n\n{_DISCLAIMER}"
        return overlay, info


def launch(config: Dict[str, Any], ckpt_path: str = None,
           model: torch.nn.Module = None, device: torch.device = None):
    demo_app = HerbDemo(config, ckpt_path, model=model, device=device)
    with gr.Blocks(title="中草药多模态识别系统",
                   theme=build_theme(), css=GLOBAL_CSS) as ui:
        # 头部：印章 + 标题横幅（新中式）
        gr.HTML(HEADER_HTML)
        # 全局医疗风险提示横幅（安全红线：见 docs/plan.md 验收项 3）
        gr.HTML(DISCLAIMER_HTML)
        with gr.Tabs():
            with gr.Tab("图片识别"):
                gr.Markdown("上传草药图片（可选）或直接输入文本描述："
                            "图片与文本都有时走多模态识别（Top-5），"
                            "只填文本时按特性检索匹配药材（Top-5）。"
                            "识别结果含毒性警示与配伍风险；置信度较低时将提示人工复核，"
                            "结果仅供科普参考。",
                            elem_classes=["tcm-hint"])
                with gr.Row():
                    img_in = gr.Image(label="上传草药图片（可选，可留空）")
                    txt_in = gr.Textbox(label="文本描述",
                                        placeholder="如：味甘平，归肝肾经，滋补肝肾、益精明目")
                btn = gr.Button("识别", variant="primary")
                with gr.Row():
                    out_pred = gr.HTML(label="识别结果")
                    out_kg = gr.HTML(label="药性说明 / 相似药 / 方剂 / 风险")
                btn.click(demo_app.predict_html, [img_in, txt_in], [out_pred, out_kg])
            with gr.Tab("特性检索"):
                gr.Markdown("输入药性/归经/功效特性，返回**所有符合的中草药**："
                            "完全匹配优先，部分匹配在后，并附毒性警示。"
                            "结果仅供科普参考，不构成用药建议。",
                            elem_classes=["tcm-hint"])
                search_in = gr.Textbox(label="特性描述", lines=2,
                                       placeholder="如：味甘平，归肝肾经，滋补肝肾、益精明目")
                search_btn = gr.Button("检索", variant="primary")
                search_out = gr.HTML(label="检索结果")
                search_btn.click(demo_app.search_text_html, search_in, search_out)
            with gr.Tab("模型关注区域 (Grad-CAM)"):
                gr.Markdown("上传图片（可选填文本），查看模型识别时**重点关注的图像部位**："
                            "红色/黄色区域即模型判断依据，用于验证模型是否真的在看草药本体。"
                            "热图仅用于模型可解释性演示，不构成识别结论或用药建议。",
                            elem_classes=["tcm-hint"])
                with gr.Row():
                    cam_img_in = gr.Image(label="上传草药图片")
                    cam_txt_in = gr.Textbox(label="文本描述(可选，留空则走纯视觉分支)",
                                            placeholder="如：味甘平，归肝肾经，滋补肝肾")
                cam_btn = gr.Button("生成热图")
                with gr.Row():
                    cam_out = gr.Image(label="关注区域热图")
                    cam_info = gr.Markdown(label="说明")
                cam_btn.click(demo_app.explain, [cam_img_in, cam_txt_in], [cam_out, cam_info])
            with gr.Tab("AI 对话"):
                gr.Markdown(
                    "与 AI 探讨药材：输入问题（如「枸杞和菊花能一起用吗？」），"
                    "系统先做**本地特性检索**提供上下文，再调用 DeepSeek 生成回答；"
                    "未配置 API Key 或调用失败时自动降级为本地知识图谱结果。"
                    "**支持图文混合问答**：可上传药材图片 + 输入问题，"
                    "系统先识别图片再结合问题回答。"
                    "对话内容仅供科普交流，含毒性药材时系统将强制警示，切勿据此自行用药。",
                    elem_classes=["tcm-hint"])
                chat_hist = gr.State([])
                # 本环境 Gradio(6.20.0) 的 Chatbot 无 type 参数，固定使用 messages 格式 list[dict]
                chat_ui = gr.Chatbot(label="AI 对话", height=420)
                with gr.Row():
                    chat_in = gr.Textbox(label="你的问题", lines=2, scale=3,
                                         placeholder="如：枸杞和菊花能一起用吗？气虚的人适合吃枸杞吗？")
                    chat_img = gr.Image(label="药材图片（可选，图文混合问答）",
                                        type="numpy", scale=2)
                with gr.Row():
                    chat_btn = gr.Button("发送", variant="primary")
                    chat_clear = gr.Button("清空对话")
                chat_btn.click(demo_app._llm_chat, [chat_in, chat_hist, chat_img],
                               [chat_ui, chat_in])
                chat_in.submit(demo_app._llm_chat, [chat_in, chat_hist, chat_img],
                               [chat_ui, chat_in])
                chat_clear.click(lambda: ([], []), None, [chat_ui, chat_hist])
            with gr.Tab("药材关系图谱"):
                gr.Markdown(
                    "可视化展示中草药知识图谱：**节点 = 药材**（颜色按功效分类，"
                    "方块 = 功效分类，三角 = 归经），**连线 = 关系**（绿 = 相须相使、"
                    "红 = 十八反、橙 = 十九畏）。选择下方药材即可聚焦其配伍与禁忌网络，"
                    "点击节点查看详情。红色描边 = 有毒药材，图谱仅供科普参考，不构成用药建议。")
                graph_choices = ["（全图浏览）"] + sorted(demo_app.kg.all_names())
                graph_dd = gr.Dropdown(
                    choices=graph_choices,
                    value="枸杞",
                    label="聚焦药材",
                    info="选择一味药查看它的配伍/禁忌关系网络；选择「（全图浏览）」查看完整图谱。",
                )
                graph_out = gr.HTML(value=build_graph_html(demo_app.kg, "枸杞"))
                graph_dd.change(
                    lambda h: build_graph_html(
                        demo_app.kg, None if h == "（全图浏览）" else h),
                    graph_dd, graph_out)
    # 端口支持环境变量覆盖（GRADIO_SERVER_PORT），避免端口被占用时无法换端口
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7862))
    # 默认只绑定本机回环地址，终端打印的 http://127.0.0.1:<port> 即为可直接访问的网址；
    # 如需局域网/手机访问，启动前设置 GRADIO_SERVER_HOST=0.0.0.0 即可。
    server_name = os.environ.get("GRADIO_SERVER_HOST", "127.0.0.1")
    # show_api=False：隐藏前端 "View API" 入口，避免浏览器触发 /info 文档路由
    # （gradio_client 1.3.0 解析该路由有 bug，已由 _patch_gradio_client_bool_bug 兜底）。
    # 注意：gradio 4.44.1 的 launch() 不支持 api_open 参数（它是 queue() 的参数），勿再传。
    try:
        ui.launch(server_name=server_name, server_port=port, show_api=False)
    except ValueError as e:
        # Gradio 4.x：当本机 localhost 被网络/代理环境判定为不可访问时，
        # 会强制要求 share=True 才能启动。此处自动回退为创建公网临时链接。
        if "shareable link" in str(e):
            print("[WARN] 检测到 localhost 不可访问，自动回退为 share=True（生成公网临时链接）。")
            ui.launch(server_name=server_name, server_port=port, share=True, show_api=False)
        else:
            raise
    print(f"\n[提示] 请在浏览器打开: http://127.0.0.1:{port}")
    if server_name == "0.0.0.0":
        import socket
        ip = socket.gethostbyname(socket.gethostname())
        print(f"[提示] 局域网设备可访问: http://{ip}:{port}")


if __name__ == "__main__":
    import yaml
    from utils.config import load_config
    cfg = load_config("experiments/configs/default_config.yaml")
    launch(cfg)
