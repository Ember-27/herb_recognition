"""导出纯视觉分类分支为 TorchScript（部署端无需 Python/文本模型/知识图谱）。

用法:
    python tools/export_model.py --out exports/vision.pt --verify

说明:
    - 仅导出 vision + vision_head（Swin 编码器 + 分类头）。
    - 输入: [B, 3, 224, 224] 的 RGB 张量（ImageNet 均值/方差归一化），
      输出: [B, num_classes] 的 logits。
    - 多模态分支含 BERT，TorchScript 兼容性差，不导出；需要完整多模态推理时
      请使用 REST API（python main.py --mode serve）。
    - 导出目录同时写入 label2idx.json，部署端可直接读取类别名。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import torch
import torch.nn as nn

from models.classifier import build_classifier
from utils.config import load_config
from utils.data_utils import build_label_maps


class VisionClassifier(nn.Module):
    """纯视觉推理封装：vision 编码器 + vision_head 分类头。"""

    def __init__(self, vision, vision_head):
        super().__init__()
        self.vision = vision
        self.vision_head = vision_head

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return self.vision_head(self.vision(images))


def export(args):
    device = torch.device("cpu")
    cfg = load_config(args.config)
    label2idx, _ = build_label_maps(cfg["data"]["train_csv"])
    num_classes = len(label2idx)

    model = build_classifier(cfg, num_classes=num_classes).to(device)
    if not os.path.exists(args.ckpt):
        print(f"[导出] 权重不存在: {args.ckpt}", file=sys.stderr)
        sys.exit(1)
    state = torch.load(args.ckpt, map_location=device)
    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"[导出] 警告: 缺失权重 {len(missing)} 个: {missing[:5]}")
    if unexpected:
        print(f"[导出] 提示: 忽略无关权重 {len(unexpected)} 个")
    model.eval()

    wrapper = VisionClassifier(model.vision, model.vision_head).eval()
    dummy = torch.randn(1, 3, cfg["image_size"], cfg["image_size"])
    # 用 trace：Swin 无动态控制流，静态图最稳妥
    scripted = torch.jit.trace(wrapper, dummy)

    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    torch.jit.save(scripted, args.out)
    with open(os.path.join(out_dir, "label2idx.json"), "w", encoding="utf-8") as f:
        json.dump(label2idx, f, ensure_ascii=False, indent=2)
    print(f"[导出] 完成 -> {args.out}  ({num_classes} 类)")

    if args.verify:
        with torch.no_grad():
            ref = wrapper(dummy)
        loaded = torch.jit.load(args.out)
        out = loaded(dummy)
        err = float((out - ref).detach().abs().max())
        ok = err < 1e-5
        print(f"[验证] 导出前后最大误差: {err:.2e} -> {'通过' if ok else '失败'}")
        if not ok:
            sys.exit(2)


def main():
    p = argparse.ArgumentParser(description="导出纯视觉分支为 TorchScript")
    p.add_argument("--config", default="experiments/configs/default_config.yaml")
    p.add_argument("--ckpt", default="experiments/checkpoints/best_model.pth")
    p.add_argument("--out", default="exports/vision.pt")
    p.add_argument("--verify", action="store_true", help="导出后加载对比验证一致性")
    args = p.parse_args()
    export(args)


if __name__ == "__main__":
    main()
