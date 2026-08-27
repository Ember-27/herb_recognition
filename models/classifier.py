"""中草药多模态分类器：组合视觉编码器、文本编码器、融合模块与分类头。"""
from typing import Dict, Any
import torch
import torch.nn as nn

from models.vision_encoder import build_vision_encoder
from models.text_encoder import build_text_encoder
from models.fusion_module import build_fusion_module


class HerbClassifier(nn.Module):
    def __init__(self, config: Dict[str, Any], num_classes: int = None):
        super().__init__()
        self.config = config
        self.vision = build_vision_encoder(config)
        self.text = build_text_encoder(config)
        self.fusion = build_fusion_module(config)
        self.embed_dim = config["fusion"]["embed_dim"]
        self.num_classes = num_classes or config.get("num_classes", 100)

        # 多模态分支分类头
        self.head = nn.Linear(self.embed_dim, self.num_classes)
        # 纯视觉保底分支：视觉特征直接接一个独立分类头。
        # 训练时与多模态分支联合优化，推理时若文本为空则直接用它，
        # 从根本上保证"即使没有文本，模型也能靠图像识别"。
        self.vision_head = nn.Linear(self.embed_dim, self.num_classes)

    def forward(self, images: torch.Tensor, texts: list, device: torch.device = None):
        """返回多模态 logits 与纯视觉 logits 的元组。

        推理辅助函数可按需选择：
          - 有文本时：取多模态 logits（融合后更准）
          - 无文本时：取 vision_logits（纯视觉保底）
        """
        device = device or next(self.parameters()).device
        v = self.vision(images.to(device))
        t = self.text(texts, device=device)
        f = self.fusion(v, t)
        logits = self.head(f)
        vision_logits = self.vision_head(v)
        return logits, vision_logits

    def forward_multimodal(self, images: torch.Tensor, texts: list, device: torch.device = None):
        return self.forward(images, texts, device=device)[0]

    @torch.no_grad()
    def predict(self, images: torch.Tensor, texts: list, device: torch.device = None):
        """推理：根据文本是否为空自动选择分支。"""
        device = device or next(self.parameters()).device
        v = self.vision(images.to(device))
        t = self.text(texts, device=device)
        has_text = any(isinstance(x, str) and x.strip() for x in texts)
        if has_text:
            f = self.fusion(v, t)
            return self.head(f)
        return self.vision_head(v)

    def get_parameter_groups(self, base_lr: float):
        """汇总各模块的分层学习率参数组。"""
        groups = []
        groups += self.vision.get_parameter_groups(base_lr, self.config["training"]["backbone_lr_multiplier"])
        groups += self.text.get_parameter_groups(base_lr)
        groups.append({"params": self.fusion.parameters(), "lr": base_lr})
        groups.append({"params": self.head.parameters(), "lr": base_lr})
        groups.append({"params": self.vision_head.parameters(), "lr": base_lr})
        return groups

    def freeze_vision_backbone(self):
        self.vision.freeze_backbone()

    def unfreeze_vision_backbone(self):
        self.vision.unfreeze_backbone()


def build_classifier(config: Dict[str, Any], num_classes: int = None) -> HerbClassifier:
    return HerbClassifier(config, num_classes)


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open("experiments/configs/default_config.yaml", encoding="utf-8"))
    model = HerbClassifier(cfg, num_classes=10)
    imgs = torch.randn(2, 3, 224, 224)
    txts = ["味甘性平", "苦寒清热"]
    logits, vision_logits = model(imgs, txts, device=torch.device("cpu"))
    print("多模态 logits 形状:", tuple(logits.shape))   # (2, 10)
    print("纯视觉 logits 形状:", tuple(vision_logits.shape))   # (2, 10)
