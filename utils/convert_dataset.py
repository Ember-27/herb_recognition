"""将外部中草药分类数据集转换为本项目的 CSV 格式 (纯标准库，无需 pandas)。

源数据集结构 (data/external/cls_chinese_medicine):
    label.txt           每行: "<int_id> <pinyin>"
    train.txt           每行: "train/<pinyin>/<file>.jpg <int_id>"
    val.txt             每行: "val/<pinyin>/<file>.jpg <int_id>"
    train/<pinyin>/...  JPEG 图片
    val/<pinyin>/...    JPEG 图片

目标 CSV 格式 (data/processed/*.csv):
    image_path,label,text
    - image_path: 相对于 data.root(=data) 的路径，直接指向外部原图，不复制 -> 省空间
    - label:       中文名（知识库有则用之，否则用拼音）
    - text:        药性描述（知识库有则用之，否则回退为中文名/拼音）

用法:
    python utils/convert_dataset.py
    python utils/convert_dataset.py --external data/external/cls_chinese_medicine
"""
import argparse
import csv
import json
import os
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXTERNAL = os.path.join(PROJECT_ROOT, "data", "external", "cls_chinese_medicine")
DEFAULT_KB = os.path.join(PROJECT_ROOT, "knowledge_graph", "herb_properties.csv")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
# CSV 中 image_path 相对 data.root(=data) 的前缀
REL_PREFIX = os.path.relpath(DEFAULT_EXTERNAL, os.path.join(PROJECT_ROOT, "data"))


def parse_label_txt(path):
    """返回 {int_id: pinyin}。"""
    id2py = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            int_id, pinyin = parts[0], parts[1]
            id2py[int(int_id)] = pinyin
    return id2py


def load_knowledge_base(path):
    """返回 {pinyin: {"chinese_name":..., "property_text":...}}。"""
    kb = {}
    if not os.path.exists(path):
        print(f"[warn] 知识库不存在: {path}，label/text 将回退为拼音。")
        return kb
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            py = (row.get("pinyin") or "").strip()
            if not py:
                continue
            kb[py] = {
                "chinese_name": (row.get("chinese_name") or "").strip(),
                "property_text": (row.get("property_text") or "").strip(),
            }
    print(f"[info] 已加载知识库: {len(kb)} 条")
    return kb


def build_text(kb_entry, pinyin):
    """按优先级产出 text: 药性 > 中文名 > 拼音。"""
    if kb_entry:
        if kb_entry.get("property_text"):
            return kb_entry["property_text"]
        if kb_entry.get("chinese_name"):
            return kb_entry["chinese_name"]
    return pinyin


def build_pinyin2label(id2py, kb):
    """为每个拼音类确定最终 label：优先中文名，冲突时用拼音消歧（按类而非按行）。"""
    # 1) 初步候选：中文名（有则用之），否则拼音
    prov = {}
    for py in id2py.values():
        entry = kb.get(py)
        prov[py] = (entry.get("chinese_name") if entry else "") or py
    # 2) 检测不同拼音映射到同一中文名的冲突，消歧
    groups = defaultdict(list)
    for py, name in prov.items():
        groups[name].append(py)
    final = dict(prov)
    for name, pys in groups.items():
        if len(pys) > 1:  # 多个拼音共用一个中文名 -> 用拼音消歧
            for py in pys:
                final[py] = f"{name}({py})"
    return final


def convert_list_file(list_file, id2py, kb, pinyin2label):
    rows = []
    with open(list_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rel_path, int_id = line.rsplit(" ", 1)
            int_id = int(int_id)
            pinyin = id2py.get(int_id, "")
            if not pinyin:
                continue
            entry = kb.get(pinyin)
            img_rel = f"{REL_PREFIX}/{rel_path}"
            rows.append({
                "image_path": img_rel,
                "label": pinyin2label[pinyin],
                "text": build_text(entry, pinyin),
            })
    return rows


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["image_path", "label", "text"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external", default=DEFAULT_EXTERNAL)
    ap.add_argument("--kb", default=DEFAULT_KB)
    args = ap.parse_args()

    label_file = os.path.join(args.external, "label.txt")
    train_file = os.path.join(args.external, "train.txt")
    val_file = os.path.join(args.external, "val.txt")
    for p in (label_file, train_file, val_file):
        if not os.path.exists(p):
            raise FileNotFoundError(f"缺少文件: {p}")

    id2py = parse_label_txt(label_file)
    print(f"[info] 类别数: {len(id2py)}")
    kb = load_knowledge_base(args.kb)

    pinyin2label = build_pinyin2label(id2py, kb)
    train_rows = convert_list_file(train_file, id2py, kb, pinyin2label)
    val_rows = convert_list_file(val_file, id2py, kb, pinyin2label)

    os.makedirs(OUT_DIR, exist_ok=True)
    train_out = os.path.join(OUT_DIR, "train.csv")
    val_out = os.path.join(OUT_DIR, "val.csv")
    write_csv(train_out, train_rows)
    write_csv(val_out, val_rows)

    # 统计
    train_labels = {r["label"] for r in train_rows}
    val_labels = {r["label"] for r in val_rows}
    print(f"[done] 训练样本: {len(train_rows)}  ({train_out})")
    print(f"[done] 验证样本: {len(val_rows)}  ({val_out})")
    print(f"[done] 训练类别: {len(train_labels)}  验证类别: {len(val_labels)}")

    # 写出 label2idx 映射，便于复现与配置校验
    classes = sorted(train_labels)
    label2idx = {c: i for i, c in enumerate(classes)}
    idx_out = os.path.join(OUT_DIR, "label2idx.json")
    with open(idx_out, "w", encoding="utf-8") as f:
        json.dump(label2idx, f, ensure_ascii=False, indent=2)
    print(f"[done] 类别映射: {idx_out}  (num_classes={len(classes)})")


if __name__ == "__main__":
    main()
