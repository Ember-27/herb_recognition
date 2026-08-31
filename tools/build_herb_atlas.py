"""构建药材图鉴：为训练集中的每一类随机抽一张图片，保存到 images/图鉴。

用法:
    python tools/build_herb_atlas.py [--root data/external] [--out images/图鉴]

- 从 train_csv（配置或默认 data/processed/train.csv）读取 中文名 -> 拼音目录名 映射
- 每类从 train/val 随机抽一张，复制/另存为 images/图鉴/<拼音名>.jpg
- 缺失样本的类别会打印警告并跳过
生成一次即可，后端识别结果固定引用该目录，保证稳定性。
"""
import argparse
import csv
import os
import random
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _strip_pinyin(name: str) -> str:
    return re.sub(r"\([^()]*\)", "", name).strip()


def build_zh2py(train_csv: str):
    """中文名 -> 拼音目录名（来自 image_path 倒数第二层）"""
    zh2py = {}
    with open(train_csv, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            zh = (row.get("label") or "").strip()
            path = row.get("image_path") or ""
            if not zh or zh in zh2py:
                continue
            parts = [p for p in path.replace("\\", "/").split("/") if p]
            if len(parts) >= 2:
                zh2py[zh] = parts[-2]
    return zh2py


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(ROOT, "data", "external"),
                    help="数据集根目录（含 cls_chinese_medicine/train,val）")
    ap.add_argument("--train-csv",
                    default=os.path.join(ROOT, "data", "processed", "train.csv"),
                    help="train_csv 路径，用于 中文名->拼音目录 映射")
    ap.add_argument("--out", default=os.path.join(ROOT, "images", "图鉴"),
                    help="图鉴输出目录")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    zh2py = build_zh2py(args.train_csv)
    print(f"[INFO] 读取到 {len(zh2py)} 个药材类别")

    exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    copied = 0
    skipped = []
    for zh, py in zh2py.items():
        candidates = []
        for sub in ("cls_chinese_medicine/train", "cls_chinese_medicine/val"):
            d = os.path.join(args.root, sub, py)
            if os.path.isdir(d):
                candidates += [os.path.join(d, f) for f in os.listdir(d)
                               if f.lower().endswith(exts)]
        if not candidates:
            skipped.append(zh)
            continue
        src = random.choice(candidates)
        dst = os.path.join(args.out, py + os.path.splitext(src)[1].lower())
        shutil.copyfile(src, dst)
        copied += 1

    print(f"[INFO] 已生成图鉴 {copied} 张 -> {args.out}")
    if skipped:
        print(f"[WARN] 未找到样本、已跳过 {len(skipped)} 类: {skipped[:20]}{'...' if len(skipped) > 20 else ''}")


if __name__ == "__main__":
    main()
