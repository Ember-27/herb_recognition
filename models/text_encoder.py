"""文本编码器：BERT-base-chinese + 投影层。

处理草药的药性/描述文本，输出与视觉编码器同维度的特征。
"""
from typing import Dict, Any, List
import os
# HuggingFace 国内镜像（默认开启，可被系统环境变量 HF_ENDPOINT 覆盖）。
# 若无法访问 huggingface.co，可通过镜像下载 bert-base-chinese 权重。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
import torch
import torch.nn as nn
from transformers import BertModel, BertTokenizer


class TextEncoder(nn.Module):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        t_cfg = config["text"]
        self.name = t_cfg.get("name", "bert-base-chinese")
        self.max_length = t_cfg.get("max_length", 64)
        self.embed_dim = config["fusion"]["embed_dim"]

        # 优先用本地路径，未配置则回退到 name（从 HuggingFace 下载）
        model_path = t_cfg.get("local_path", self.name)
        self.tokenizer = BertTokenizer.from_pretrained(model_path)
        self.bert = BertModel.from_pretrained(model_path)

        # 文本编码器冻结策略：
        #   freeze=true            -> 冻结整个 BERT，仅训练投影层（默认，省算力）
        #   freeze=false           -> 解冻最后 unfreeze_layers 层 + 池化层，其余冻结
        #   unfreeze_layers        -> 解冻 BERT 最后 N 层（如 4 表示后 4 层可训练）
        self.freeze = t_cfg.get("freeze", True)
        self.unfreeze_layers = int(t_cfg.get("unfreeze_layers", 4))
        if self.freeze:
            for p in self.bert.parameters():
                p.requires_grad = False
        else:
            # 默认冻结全部，再逐层解冻最后 unfreeze_layers 层
            for p in self.bert.parameters():
                p.requires_grad = False
            layers = self.bert.encoder.layer
            for layer in layers[-self.unfreeze_layers:]:
                for p in layer.parameters():
                    p.requires_grad = True
            # 池化层也解冻，便于适配药材领域文本
            for p in self.bert.pooler.parameters():
                p.requires_grad = True

        self.proj = nn.Linear(self.bert.config.hidden_size, self.embed_dim)

    @torch.no_grad()
    def _encode_texts(self, texts: List[str], device: torch.device) -> dict:
        enc = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        return {k: v.to(device) for k, v in enc.items()}

    def forward(self, texts: List[str], device: torch.device = None) -> torch.Tensor:
        device = device or next(self.parameters()).device
        enc = self._encode_texts(texts, device)
        out = self.bert(**enc)
        cls = out.last_hidden_state[:, 0]   # [B, hidden_size]
        return self.proj(cls)               # [B, embed_dim]

    def get_parameter_groups(self, base_lr: float):
        # BERT 冻结时仅投影层可训练；解冻时把可训练参数用较低 lr 加入
        groups = [{"params": self.proj.parameters(), "lr": base_lr}]
        if not self.freeze:
            bert_params = [p for p in self.bert.parameters() if p.requires_grad]
            if bert_params:
                groups.append({"params": bert_params, "lr": base_lr * 0.1})
        return groups


def build_text_encoder(config: Dict[str, Any]) -> TextEncoder:
    return TextEncoder(config)


if __name__ == "__main__":
    cfg = {"text": {"name": "bert-base-chinese", "max_length": 64, "freeze": True},
           "fusion": {"embed_dim": 512}}
    model = TextEncoder(cfg)
    out = model(["味甘性平，归肝肾经", "清热解毒，凉血利咽"], device=torch.device("cpu"))
    print("输出特征形状:", tuple(out.shape))   # (2, 512)
