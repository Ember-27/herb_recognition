"""纯视觉链路增强版诊断：定位"抽检 10% vs 官方 eval 86%"的矛盾。

与 check_vision.py 的区别：
  1. 先做标签映射自检（train/val 类别集合是否一致）
  2. 权重加载后打印状态字典大小/随机性检查
  3. 每张图同时报：纯视觉 Top-5、真值在纯视觉中的排名、融合 Top-1
  4. 用 model.forward() 交叉验证 predict() 的纯视觉分支结果一致
  5. 汇总置信度分布与真值排名统计

用法:
    venv312\\Scripts\\python.exe check_vision_pro.py --num 20
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
    p = argparse.ArgumentParser(description="纯视觉链路增强版诊断")
    p.add_argument("--config", default="experiments/configs/default_config.yaml")
    p.add_argument("--ckpt", default="experiments/checkpoints/best_model.pth")
    p.add_argument("--csv", default=None, help="抽样来源 CSV（默认用配置里的 val_csv）")
    p.add_argument("--num", type=int, default=20, help="抽样张数")
    p.add_argument("--topk", type=int, default=5, help="Top-k 展示与统计")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    cfg = load_config(args.config)
    device = get_device(cfg["device"])
    csv_path = args.csv or cfg["data"]["val_csv"]

    # ---------- 1) 标签映射自检 ----------
    import pandas as pd
    tr = pd.read_csv(cfg["data"]["train_csv"])
    va = pd.read_csv(csv_path)
    tr_labels, va_labels = set(tr["label"]), set(va["label"])
    print(f"[1] train 类别数={len(tr_labels)}  val 类别数={len(va_labels)}  val 行数={len(va)}")
    miss = va_labels - tr_labels
    if miss:
        print(f"    [!!!] val 有 {len(miss)} 个类别不在 train: {sorted(miss)[:20]}")
    else:
        print("    类别集合一致 -> label 索引错位风险降低")

    label2idx, idx2label = build_label_maps(cfg["data"]["train_csv"])
    model = build_classifier(cfg, num_classes=len(idx2label)).to(device)
    print(f"[2] 权重: {args.ckpt}")
    if os.path.exists(args.ckpt):
        sd = torch.load(args.ckpt, map_location=device)
        model.load_state_dict(sd)
        print(f"    state_dict 加载成功 (key 数={len(sd)})  num_classes={len(idx2label)}")
        # 权重随机性检查：看 vision_head 与 head 的权重范数
        with torch.no_grad():
            vh_norm = model.vision_head.weight.norm().item()
            h_norm = model.head.weight.norm().item()
        print(f"    vision_head.weight L2={vh_norm:.4f}  head.weight L2={h_norm:.4f}"
              f"  (随机初始化通常 ~{np.sqrt(1 / 512) * np.sqrt(163):.2f})")
    else:
        print("    [!!!] 未找到权重，使用随机初始化模型")
    model.eval()

    # ---------- 2) 逐张抽检 ----------
    transform = A.Compose([
        A.Resize(cfg["image_size"], cfg["image_size"]),
        A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ToTensorV2(),
    ])
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    rows = random.sample(rows, min(args.num, len(rows)))

    vh1 = vh5 = fh1 = 0
    max_probs = []
    gt_ranks = []
    branch_mismatch = 0
    print(f"\n[3] 抽样 {len(rows)} 张 | 来源: {csv_path}\n")

    for row in rows:
        img_path = os.path.join(cfg["data"]["root"], row["image_path"].replace("\\", "/"))
        try:
            img = np.array(Image.open(img_path).convert("RGB"))
        except Exception as e:
            print(f"    [IMG-ERR] {img_path}: {e}")
            continue
        tensor = transform(image=img)["image"].unsqueeze(0)
        gt = row["label"]
        with torch.no_grad():
            # predict() 的纯视觉分支
            v_logits = model.predict(tensor, [""], device=device)[0]
            # predict() 的融合分支（用真值文本）
            f_logits = model.predict(tensor, [gt], device=device)[0]
            # forward() 交叉验证：两个分支同时出
            mm_logits, vis_logits = model(tensor, [""], device=device)
            vis_logits = vis_logits[0]

        # 交叉验证：predict() 的纯视觉结果是否等于 forward() 的 vision_head 输出
        if not torch.allclose(v_logits, vis_logits, atol=1e-4):
            branch_mismatch += 1

        v_probs = torch.softmax(v_logits, dim=0)
        f_probs = torch.softmax(f_logits, dim=0)
        max_probs.append(float(v_probs.max()))
        gt_idx = label2idx.get(gt, -1)
        # 真值在纯视觉概率中的排名（1=第一）
        gt_rank = int((v_probs >= v_probs[gt_idx]).sum().item()) if gt_idx >= 0 else -1
        gt_ranks.append(gt_rank)

        v_topk = torch.topk(v_probs, min(args.topk, len(v_probs)))
        f_top1 = int(torch.topk(f_probs, 1).indices[0])
        v_cands = "  ".join(f"{idx2label[int(i)]}({float(p)*100:.0f}%)"
                            for i, p in zip(v_topk.indices, v_topk.values))
        v_ok = idx2label[int(v_topk.indices[0])] == gt
        v_ok5 = gt in {idx2label[int(i)] for i in v_topk.indices}
        f_ok = idx2label[f_top1] == gt
        vh1 += v_ok
        vh5 += v_ok5
        fh1 += f_ok
        print(f"[{'OK ' if v_ok else 'NO '}] 真值={gt}")
        print(f"    纯视觉Top{args.topk}: {v_cands}")
        print(f"    真值排名(纯视觉)=#{gt_rank}/{len(v_probs)}  融合Top1={idx2label[f_top1]} "
              f"({'OK' if f_ok else 'NO'})")

    n = len(rows)
    mp = np.array(max_probs)
    gr = np.array(gt_ranks)
    print(f"\n===== 汇总 (n={n}) =====")
    print(f"纯视觉 Top-1: {vh1}/{n} = {vh1/n*100:.1f}%   Top-{args.topk}: {vh5}/{n} = {vh5/n*100:.1f}%")
    print(f"融合   Top-1: {fh1}/{n} = {fh1/n*100:.1f}%")
    print(f"纯视觉 Top-1 置信度: min={mp.min():.4f}  均值={mp.mean():.4f}  max={mp.max():.4f}")
    print(f"真值排名: 均值={gr.mean():.1f}  中位={np.median(gr):.0f}  "
          f"<=5 占 {(gr <= 5).mean()*100:.1f}%  <=20 占 {(gr <= 20).mean()*100:.1f}%")
    if branch_mismatch:
        print(f"[!!!] predict() 与 forward() 纯视觉分支不一致次数: {branch_mismatch}")
    print("  解读: 置信度均值≈1/163(0.006) -> 权重未生效/随机模型；")
    print("        置信度高但类别错且真值排名低 -> 模型没认对图；")
    print("        置信度高且真值排名高(<=20)但Top1错 -> label 索引错位/近混淆类")


if __name__ == "__main__":
    main()
