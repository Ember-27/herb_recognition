"""纯视觉链路抽检脚本。

从 val 集随机抽 N 张图，只看图（不填文本）跑 Top-k 预测，
统计 Top-1 / Top-5 命中率，用来判断"随便抽几张都不对"到底是
模型问题还是"纯视觉本来就只有这个水平"。

可选 --with-text：用真值药名作为文本再跑一次融合分支，做对照，
证明多模态链路是正常的。

用法:
    venv312\\Scripts\\python.exe check_vision.py                       # 默认抽 10 张
    venv312\\Scripts\\python.exe check_vision.py --num 30 --topk 5     # 抽 30 张
    venv312\\Scripts\\python.exe check_vision.py --with-text           # 纯视觉 + 融合对照
"""
import os
import csv
import random
import argparse

import numpy as np
import torch
import albumentations as A
from albumentations.pytorch import ToTensorV2
from PIL import Image

from models.classifier import build_classifier
from utils.config import load_config, get_device
from utils.data_utils import build_label_maps


def parse_args():
    p = argparse.ArgumentParser(description="纯视觉链路抽检")
    p.add_argument("--config", default="experiments/configs/default_config.yaml")
    p.add_argument("--ckpt", default="experiments/checkpoints/best_model.pth")
    p.add_argument("--csv", default=None, help="抽样来源 CSV（默认用配置里的 val_csv）")
    p.add_argument("--num", type=int, default=10, help="抽样张数")
    p.add_argument("--topk", type=int, default=5, help="Top-k 展示与统计")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--with-text", action="store_true",
                   help="同时用真值药名作为文本跑融合分支，做对照")
    return p.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    cfg = load_config(args.config)
    device = get_device(cfg["device"])
    csv_path = args.csv or cfg["data"]["val_csv"]

    _, idx2label = build_label_maps(cfg["data"]["train_csv"])
    model = build_classifier(cfg, num_classes=len(idx2label)).to(device)
    if os.path.exists(args.ckpt):
        model.load_state_dict(torch.load(args.ckpt, map_location=device))
        print(f"[INFO] 已加载权重: {args.ckpt}")
    else:
        print(f"[WARN] 未找到权重: {args.ckpt}，使用随机初始化模型（置信度≈1/类别数）")
    model.eval()

    transform = A.Compose([
        A.Resize(cfg["image_size"], cfg["image_size"]),
        A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ToTensorV2(),
    ])

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    rows = random.sample(rows, min(args.num, len(rows)))
    print(f"\n抽样 {len(rows)} 张 | 来源: {csv_path}\n")

    vis_hit1 = vis_hit5 = 0
    txt_hit1 = txt_hit5 = 0
    for row in rows:
        img_path = os.path.join(cfg["data"]["root"], row["image_path"].replace("\\", "/"))
        tensor = transform(image=np.array(Image.open(img_path).convert("RGB")))["image"].unsqueeze(0)
        gt = row["label"]

        # 纯视觉分支（文本传空字符串）
        with torch.no_grad():
            v_logits = model.predict(tensor, [""], device=device)[0]
        v_topk = torch.topk(torch.softmax(v_logits, dim=0), args.topk)
        v_cands = "  ".join(f"{idx2label[int(i)]}({float(v)*100:.0f}%)"
                            for i, v in zip(v_topk.indices, v_topk.values))
        v_ok = idx2label[int(v_topk.indices[0])] == gt
        v_ok5 = gt in {idx2label[int(i)] for i in v_topk.indices}
        vis_hit1 += v_ok
        vis_hit5 += v_ok5
        line = f"[{'OK ' if v_ok else 'NO '}] 真值={gt:6s} | 纯视觉Top{args.topk}: {v_cands}"

        # 融合分支对照（用真值药名作为文本）
        if args.with_text:
            with torch.no_grad():
                f_logits = model.predict(tensor, [gt], device=device)[0]
            f_topk = torch.topk(torch.softmax(f_logits, dim=0), args.topk)
            f_ok = idx2label[int(f_topk.indices[0])] == gt
            f_ok5 = gt in {idx2label[int(i)] for i in f_topk.indices}
            txt_hit1 += f_ok
            txt_hit5 += f_ok5
            f_mark = "OK" if f_ok else "NO"
            line += f"\n            [融合·文本={gt}] Top1={idx2label[int(f_topk.indices[0])]} {f_mark}"

        print(line)

    n = len(rows)
    print(f"\n===== 汇总 (n={n}) =====")
    print(f"纯视觉 Top-1 : {vis_hit1}/{n} = {vis_hit1 / n * 100:.1f}%")
    print(f"纯视觉 Top-{args.topk}: {vis_hit5}/{n} = {vis_hit5 / n * 100:.1f}%")
    if args.with_text:
        print(f"融合(真值文本) Top-1 : {txt_hit1}/{n} = {txt_hit1 / n * 100:.1f}%")
        print(f"融合(真值文本) Top-{args.topk}: {txt_hit5}/{n} = {txt_hit5 / n * 100:.1f}%")


if __name__ == "__main__":
    main()
