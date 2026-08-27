"""视觉编码器：支持多种 backbone，按显存排序，输出固定维度特征。

默认使用 Swin-Tiny (8G 显存友好，约 5-6GB)。
"""
from typing import Dict, Any
import torch
import torch.nn as nn
from timm import create_model


# 按显存占用从小到大排序的可用模型
AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "efficientnet_b0": dict(factory="efficientnet_b0", vram_gb=1.8, note="最轻量"),
    "resnet50":       dict(factory="resnet50",       vram_gb=2.4, note="经典稳定"),
    "swin_tiny":      dict(factory="swin_tiny_patch4_window7_224", vram_gb=2.6, note="默认推荐(8G)"),
    "convnext_tiny":  dict(factory="convnext_tiny",  vram_gb=2.8, note="现代 CNN"),
}


class VisionEncoder(nn.Module):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        v_cfg = config["vision"]
        name = v_cfg.get("name", "swin_tiny")
        embed_dim = config["fusion"]["embed_dim"]
        if name not in AVAILABLE_MODELS:
            raise ValueError(f"不支持的视觉模型: {name}，可选: {list(AVAILABLE_MODELS)}")

        # num_classes=0 让 timm 返回分类层之前的特征
        self.backbone = create_model(
            AVAILABLE_MODELS[name]["factory"],
            pretrained=v_cfg.get("pretrained", True),
            num_classes=0,
        )
        self.feat_dim = self.backbone.num_features
        # 投影到统一的融合维度
        self.proj = nn.Linear(self.feat_dim, embed_dim)
        self.embed_dim = embed_dim

    def extract_features(self, x: torch.Tensor) -> torch.Tensor:
        f = self.backbone(x)          # [B, feat_dim]
        return self.proj(f)           # [B, embed_dim]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.extract_features(x)

    def freeze_backbone(self):
        """冻结主干，仅训练投影层（小数据集热身用）。"""
        for p in self.backbone.parameters():
            p.requires_grad = False

    def unfreeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = True

    def get_parameter_groups(self, base_lr: float, backbone_mult: float = 0.1):
        """分层学习率：主干用较小 lr，投影层用 base_lr。"""
        backbone_params, head_params = [], []
        for n, p in self.named_parameters():
            if "backbone" in n:
                backbone_params.append(p)
            else:
                head_params.append(p)
        return [
            {"params": backbone_params, "lr": base_lr * backbone_mult},
            {"params": head_params, "lr": base_lr},
        ]

    @staticmethod
    def estimate_memory(batch_size: int, image_size: int) -> float:
        """粗略估算训练显存 (GB)，用于环境检查。"""
        # 经验公式：每张图 ~ (image_size/224)^2 * batch_size * 0.02 GB (含激活/梯度)
        return 1.2 + (image_size / 224) ** 2 * batch_size * 0.02 * 4


def build_vision_encoder(config: Dict[str, Any]) -> VisionEncoder:
    return VisionEncoder(config)


if __name__ == "__main__":
    # 快速自测
    cfg = {"vision": {"name": "swin_tiny"}, "fusion": {"embed_dim": 512}}
    model = VisionEncoder(cfg)
    x = torch.randn(2, 3, 224, 224)
    out = model(x)
    print("输出特征形状:", tuple(out.shape))   # 应为 (2, 512)
    print("估算显存(bs=16,224):", round(VisionEncoder.estimate_memory(16, 224), 2), "GB")
