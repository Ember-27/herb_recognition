"""跨模态融合模块 (HCA-Fusion: 层级式跨模态注意力融合)。

模拟中医"望闻问切"的互补判断：视觉(token)与文本(token)互为 query/key/value
进行交叉注意力，再经自注意力精炼，最后池化为统一表征。
"""
from typing import Dict, Any
import torch
import torch.nn as nn


class HCAFusion(nn.Module):
    def __init__(self, embed_dim: int, num_heads: int = 8):
        super().__init__()
        self.embed_dim = embed_dim
        self.cross_v2t = nn.MultiheadAttention(embed_dim, num_heads, batch_first=True)
        self.cross_t2v = nn.MultiheadAttention(embed_dim, num_heads, batch_first=True)
        self.self_attn = nn.MultiheadAttention(embed_dim, num_heads, batch_first=True)
        self.norm1 = nn.LayerNorm(embed_dim)
        self.norm2 = nn.LayerNorm(embed_dim)
        self.norm3 = nn.LayerNorm(embed_dim)

    def forward(self, vision: torch.Tensor, text: torch.Tensor) -> torch.Tensor:
        # vision/text: [B, embed_dim] -> 视作单 token 序列 [B, 1, embed_dim]
        v = vision.unsqueeze(1)
        t = text.unsqueeze(1)

        # 交叉注意力：视觉关注文本，文本关注视觉
        v_att, _ = self.cross_v2t(query=v, key=t, value=t)
        t_att, _ = self.cross_t2v(query=t, key=v, value=v)
        v = self.norm1(v + v_att)
        t = self.norm1(t + t_att)

        # 拼接后经自注意力精炼
        fused = torch.cat([v, t], dim=1)            # [B, 2, embed_dim]
        fused, _ = self.self_attn(query=fused, key=fused, value=fused)
        fused = self.norm2(fused)

        # 池化为 [B, embed_dim]
        fused = fused.mean(dim=1)
        return self.norm3(fused)


def build_fusion_module(config: Dict[str, Any]) -> HCAFusion:
    f_cfg = config["fusion"]
    return HCAFusion(f_cfg["embed_dim"], f_cfg.get("num_heads", 8))


if __name__ == "__main__":
    cfg = {"fusion": {"embed_dim": 512, "num_heads": 8}}
    fusion = HCAFusion(cfg["fusion"]["embed_dim"], cfg["fusion"]["num_heads"])
    v = torch.randn(4, 512)
    t = torch.randn(4, 512)
    out = fusion(v, t)
    print("融合输出形状:", tuple(out.shape))   # (4, 512)
