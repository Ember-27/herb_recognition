"""从原始训练/验证集中按"每类 N 张"抽取子集，构成新数据集。

特点：
  - 完全保留原始图片文件，只重新生成 CSV 索引（不复制/不删除原图）。
  - 支持按比例或按固定张数抽取，可分别指定训练/验证每类张数。
  - 若某类原始样本不足，则取该类的全部样本。

用法示例：
  python tools/subsample_dataset.py --per-class-train 120 --per-class-val 20
  python tools/subsample_dataset.py --ratio 0.1 --per-class-val 20
"""
import argparse
import csv
import random
from pathlib import Path

ROOT = Path("data/processed")


def load_csv(path: Path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_csv(path: Path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def group_by_label(rows):
    by_label = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)
    return by_label


def subsample(args):
    random.seed(args.seed)

    src_train = load_csv(ROOT / "train.csv")
    src_val = load_csv(ROOT / "val.csv") if (ROOT / "val.csv").exists() else []

    train_by_label = group_by_label(src_train)
    val_by_label = group_by_label(src_val)
    all_labels = sorted(train_by_label)

    train_out, val_out = [], []
    skipped = 0

    per_class_train = args.per_class_train
    per_class_val = args.per_class_val

    for lab in all_labels:
        # ---- 训练子集：优先从原 train.csv 抽 ----
        t_samples = train_by_label[lab]
        random.shuffle(t_samples)
        if per_class_train is None:
            n_tr = max(1, int(len(t_samples) * args.ratio))
        else:
            n_tr = min(len(t_samples), per_class_train)
        train_out += t_samples[:n_tr]

        # ---- 验证子集：优先从原 val.csv 抽，不足再从 train 剩余补 ----
        v_pool = val_by_label.get(lab, [])
        random.shuffle(v_pool)
        if per_class_val is None:
            n_v = max(1, int(len(v_pool) * args.ratio)) if v_pool else 0
        else:
            n_v = min(len(v_pool), per_class_val)

        need = per_class_val - n_v if per_class_val is not None else 0
        if need > 0:
            # 用 train 里没被训练子集取走的样本补充
            remain = t_samples[n_tr:]
            random.shuffle(remain)
            extra = remain[:need]
            v_pool = v_pool + extra
            n_v = min(len(v_pool), per_class_val if per_class_val else len(v_pool))
        val_out += v_pool[:n_v]

        if n_tr == 0:
            skipped += 1

    # 打乱顺序，避免同类连续（有助于 batch 内类别混合）
    random.shuffle(train_out)
    random.shuffle(val_out)

    save_csv(ROOT / "train_sub.csv", train_out, ["image_path", "label", "text"])
    save_csv(ROOT / "val_sub.csv", val_out, ["image_path", "label", "text"])

    print(f"类别总数: {len(all_labels)} (跳过空类: {skipped})")
    print(f"新训练集: {len(train_out)} 张 (CSV: data/processed/train_sub.csv)")
    print(f"新验证集: {len(val_out)} 张 (CSV: data/processed/val_sub.csv)")


def main():
    p = argparse.ArgumentParser(description="子采样中草药数据集（保留原图，只生成新 CSV）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--per-class-train", type=int, help="每类训练样本数（固定张数）")
    g.add_argument("--ratio", type=float, help="按原始训练集比例抽取（0~1）")
    p.add_argument("--per-class-val", type=int, default=20, help="每类验证样本数，默认 20")
    p.add_argument("--seed", type=int, default=42, help="随机种子")
    args = p.parse_args()
    subsample(args)


if __name__ == "__main__":
    main()
