"""收藏夹持久化存储：以 JSON 文件（data/favorites.json）保存用户收藏的药材与对话。

数据结构:
    {
      "herbs": [ { "fid": str, "name": str, "info": {...}, "ts": float }, ... ],
      "chats": [ { "fid": str, "question": str, "answer": str,
                   "rag_sources": [...], "ts": float }, ... ]
    }

叶子字段均按需写入；读取与写入均加锁，避免并发损坏。
"""
import json
import os
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, "data", "favorites.json")
_LOCK = threading.Lock()


def _load() -> dict:
    if not os.path.isfile(DATA_FILE):
        return {"herbs": [], "chats": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"herbs": [], "chats": []}
        data.setdefault("herbs", [])
        data.setdefault("chats", [])
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {"herbs": [], "chats": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def load_favorites() -> dict:
    with _LOCK:
        return _load()


def add_herb(name: str, info: dict = None):
    """收藏药材，按 name 去重；已存在则返回 {"duplicate": True, ...}。"""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "empty_name"}
    with _LOCK:
        data = _load()
        for h in data["herbs"]:
            if h.get("name") == name:
                return {"ok": True, "duplicate": True, "favorite": h}
        fav = {"fid": uuid.uuid4().hex[:12], "name": name,
               "info": info or {}, "ts": time.time()}
        data["herbs"].insert(0, fav)
        _save(data)
        return {"ok": True, "duplicate": False, "favorite": fav}


def add_chat(question: str, answer: str, rag_sources=None, image=None):
    """收藏一条对话记录；question+answer 同时为空则忽略。

    image 可为 base64 data URL 字符串（含 "data:image/...;base64," 前缀）或原始 bytes。
    为持久化跨会话查看，统一转存为 data URL 写入 JSON。
    """
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question and not answer:
        return {"ok": False, "error": "empty_chat"}
    image_data_url = None
    if image:
        try:
            if isinstance(image, bytes):
                import base64
                image_data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
            elif isinstance(image, str) and image.startswith("data:image/"):
                image_data_url = image
        except Exception:
            image_data_url = None
    with _LOCK:
        data = _load()
        fav = {"fid": uuid.uuid4().hex[:12], "question": question,
               "answer": answer, "rag_sources": rag_sources or [],
               "image": image_data_url, "ts": time.time()}
        data["chats"].insert(0, fav)
        _save(data)
        return {"ok": True, "favorite": fav}


def remove_favorite(fid: str) -> dict:
    """按 fid 删除收藏（药材或对话通用）。返回是否命中。"""
    with _LOCK:
        data = _load()
        prev = len(data["herbs"]) + len(data["chats"])
        data["herbs"] = [h for h in data["herbs"] if h.get("fid") != fid]
        data["chats"] = [c for c in data["chats"] if c.get("fid") != fid]
        hit = (prev != len(data["herbs"]) + len(data["chats"]))
        if hit:
            _save(data)
        return {"ok": True, "hit": hit}


def clear_favorites(kind: str = None) -> dict:
    """清空收藏；kind 可为 "herb" / "chat" / None（全部）。"""
    with _LOCK:
        data = _load()
        if kind == "herb":
            data["herbs"] = []
        elif kind == "chat":
            data["chats"] = []
        else:
            data["herbs"] = []
            data["chats"] = []
        _save(data)
        return {"ok": True}
