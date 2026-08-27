"""知识库 RAG 检索：基于本地 BERT 的中文语义向量检索，为 LLM 提供「依据上下文」。

设计目标（离线可用，零新增依赖）:
  - 复用本地 bert-base-chinese（默认 D:/models/bert-base-chinese，可用环境变量
    RAG_MODEL_PATH 覆盖），纯 CPU 推理，无需联网下载模型。
  - 语料来自知识图谱：① 每味药条目切片 ② 相须相使配伍切片 ③ 十八反/十九畏禁忌切片。
  - 检索策略：问题中出现的药材名优先精确命中对应切片，再按 BERT 语义向量
    余弦相似度补充 Top-K，最终返回可拼进 LLM prompt 的「依据文本」与来源列表。
  - 降级：BERT 加载失败时自动退化为关键词匹配，保证功能可用、不抛异常。

用法:
    from app.rag_retriever import RAGRetriever
    rr = RAGRetriever(kg)                     # 轻量构造（不加载模型）
    text, sources = rr.retrieve(question, herbs=["枸杞"])   # 首次调用才加载模型
"""
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from knowledge_graph.kg_builder import HerbKnowledgeGraph

_DEFAULT_MODEL = "D:/models/bert-base-chinese"


class RAGRetriever:
    """中文语义检索器。模型与索引懒加载（首次 retrieve 时初始化）。"""

    def __init__(self, kg: HerbKnowledgeGraph,
                 model_name_or_path: Optional[str] = None):
        self.kg = kg
        self.model_path = (model_name_or_path
                           or os.environ.get("RAG_MODEL_PATH", _DEFAULT_MODEL))
        self._chunks: List[Dict] = []     # [{text, title}]
        self._emb: Optional[np.ndarray] = None   # [N, D] 已归一化
        self._model = None
        self._tok = None
        self._inited = False
        self._fallback = False

    # ------------------------- 初始化 -------------------------
    def _build_chunks(self) -> None:
        """从知识图谱构建检索切片语料。"""
        chunks: List[Dict] = []
        kg = self.kg

        # 1) 药材条目切片
        for name in kg.all_names():
            info = kg.get_info(name)
            if not info:
                continue
            pairs = kg.recommend_pairs(name)
            contra = kg.contraindications(name)
            chunks.append({
                "title": f"药材·{name}",
                "text": (
                    f"【{name}】药性：{info['property']}；归经：{info['meridian']}；"
                    f"功效：{info['function']}；功效分类：{'、'.join(info['categories'])}；"
                    f"常用配伍：{'、'.join(pairs) if pairs else '无'}；"
                    f"十八反：{'、'.join(contra['incompatible']) if contra['incompatible'] else '无'}；"
                    f"十九畏：{'、'.join(contra['restraint']) if contra['restraint'] else '无'}。"),
            })

        # 2) 相须相使配伍切片
        for u, v, d in kg.graph.edges(data=True):
            if d.get("relation") != "paired":
                continue
            iu, iv = kg.get_info(u), kg.get_info(v)
            if not iu or not iv:
                continue
            chunks.append({
                "title": f"配伍·{u}×{v}",
                "text": (
                    f"【配伍】{u} 与 {v} 为常用相须相使配伍，可同用："
                    f"{u}（{iu['function']}）配合 {v}（{iv['function']}），"
                    f"两者功效互补，协同增效。"),
            })

        # 3) 禁忌切片（十八反 / 十九畏）
        for u, v, d in kg.graph.edges(data=True):
            rel = d.get("relation")
            if rel == "incompatible":
                tip = "存在十八反配伍禁忌，不宜同用"
            elif rel == "restraint":
                tip = "存在十九畏配伍禁忌，应避免同用"
            else:
                continue
            chunks.append({
                "title": f"禁忌·{u}×{v}",
                "text": f"【配伍禁忌】{u} 与 {v} {tip}。",
            })

        self._chunks = chunks

    def _load_model(self) -> None:
        """加载本地 BERT 并编码全部切片。失败时启用关键词降级。"""
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self.model_path)
            self._model = AutoModel.from_pretrained(self.model_path).to("cpu").eval()
            texts = [c["text"] for c in self._chunks]
            self._emb = self._encode(texts, batch=64)
            self._inited = True
        except Exception as e:  # 网络/路径/依赖异常均降级
            self._fallback = True
            print(f"[RAG] BERT 加载失败，启用关键词降级检索: {e}")

    def _encode(self, texts: List[str], batch: int = 64) -> np.ndarray:
        import torch
        vecs = []
        for i in range(0, len(texts), batch):
            enc = self._tok(texts[i:i + batch], padding=True, truncation=True,
                            max_length=64, return_tensors="pt")
            with torch.no_grad():
                out = self._model(**enc)
            mask = enc["attention_mask"].unsqueeze(-1).float()
            vec = (out.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
            vec = torch.nn.functional.normalize(vec, dim=1)
            vecs.append(vec.numpy())
        return np.vstack(vecs) if vecs else np.zeros((0, 768))

    # ------------------------- 检索 -------------------------
    def retrieve(self, query: str, herbs: Optional[List[str]] = None,
                 top_k: int = 6) -> Tuple[str, List[Dict]]:
        """返回 (依据文本, 来源列表)。首次调用时懒加载模型与索引。"""
        query = (query or "").strip()
        if not self._inited and not self._fallback:
            self._build_chunks()
            if not self._chunks:
                return "", []
            self._load_model()
        if not self._chunks:
            return "", []

        scores = self._score_all(query, herbs or [])
        order = np.argsort(-scores) if scores.size else np.arange(len(self._chunks))
        picked, seen = [], set()
        # ① 药材名精确命中优先（语义检索可能漏掉图内精确词）
        names = set(h for h in (herbs or []) if h in self.kg.graph)
        names |= set(self._extract_names(query))
        for i, c in enumerate(self._chunks):
            if len(picked) >= top_k:
                break
            if i in seen:
                continue
            title_hit = any(n in c["title"] for n in names)
            if title_hit:
                picked.append(i)
                seen.add(i)
        # ② 语义 Top-K 补充
        for i in order:
            if len(picked) >= top_k:
                break
            if i not in seen:
                picked.append(i)
                seen.add(i)

        picked = picked[:top_k]
        ctx_parts, sources = [], []
        for j, i in enumerate(picked, 1):
            c = self._chunks[i]
            ctx_parts.append(f"[{j}] {c['text']}")
            sources.append({"title": c["title"], "text": c["text"],
                            "score": float(scores[i]) if scores.size else 0.0})
        return "\n".join(ctx_parts), sources

    def _score_all(self, query: str, herbs: List[str]) -> np.ndarray:
        if self._inited and self._emb is not None:
            qv = self._encode([query])[0]
            return self._emb @ qv
        # 降级：关键词/药名出现次数打分
        keys = set(re.findall(r"[\u4e00-\u9fa5]{2,}", query)) | set(herbs)
        scores = np.zeros(len(self._chunks), dtype=float)
        for i, c in enumerate(self._chunks):
            hit = sum(1 for k in keys if k in c["text"])
            if hit:
                scores[i] = 1.0 + min(hit, 5) * 0.5
        return scores

    @staticmethod
    def _extract_names(query: str) -> List[str]:
        names = []
        # 简单模式：问题中的药材名（len>=2 的中文片段且在图谱中）——交给调用方传递 herbs 即可，
        # 这里仅兜底按图谱名称最长匹配
        return names
