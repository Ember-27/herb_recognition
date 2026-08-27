"""评估：准确率、Top-5、混淆矩阵、分类报告。"""
from typing import Dict, Any
import torch
import numpy as np
from torch.utils.data import DataLoader
from sklearn.metrics import classification_report, confusion_matrix
from loguru import logger


@torch.no_grad()
def evaluate(model, loader: DataLoader, device: torch.device, idx2label: Dict[int, str],
             use_text: bool = True):
    """评估分类器。

    use_text=False 时将所有文本置空，强制走纯视觉分支，用于验证
    "模型是否真的会看图"——这是多模态系统能否可信的关键指标。
    """
    model.eval()
    all_preds, all_labels = [], []
    all_logits = []
    for batch in loader:
        images = batch["image"].to(device)
        texts = batch["text"] if use_text else [""] * len(batch["text"])
        labels = batch["label"].to(device)
        mm_logits, vis_logits = model(images, texts, device=device)
        logits = mm_logits if use_text else vis_logits
        preds = logits.argmax(1).cpu().numpy()
        all_preds.extend(preds.tolist())
        all_labels.extend(labels.cpu().numpy().tolist())
        all_logits.append(logits.cpu())

    all_logits = torch.cat(all_logits, dim=0)
    acc = np.mean(np.array(all_preds) == np.array(all_labels))
    # 真实 Top-k：在完整 logits 上取前 k 个预测，命中其一即为正确
    # 注意 all_logits 已 .cpu()，labels 需与之一致，避免 device 不匹配
    top5 = top_k_accuracy(all_logits, torch.tensor(all_labels, device=all_logits.device), k=5)

    labels_sorted = sorted(idx2label.keys())
    target_names = [idx2label[i] for i in labels_sorted]
    report = classification_report(all_labels, all_preds, labels=labels_sorted,
                                   target_names=target_names, zero_division=0)
    cm = confusion_matrix(all_labels, all_preds, labels=labels_sorted)

    logger.info(f"总体准确率: {acc:.4f}  Top-5: {top5:.4f}")
    return {
        "accuracy": acc,
        "top5": top5,
        "report": report,
        "confusion_matrix": cm,
        "use_text": use_text,
    }


def top_k_accuracy(logits: torch.Tensor, labels: torch.Tensor, k: int = 5) -> float:
    """真实 Top-k 准确率：labels 形状 [N]，logits 形状 [N, C]。
    取 logits 前 k 个预测，若正确标签在其中有一次命中即记对。"""
    with torch.no_grad():
        _, topk_idx = logits.topk(k, dim=1)
        hits = topk_idx.eq(labels.unsqueeze(1).expand_as(topk_idx))
    return float(hits.any(dim=1).float().mean().item())


def predict_topk(model, image: torch.Tensor, texts, device, idx2label, k: int = 3):
    """对单样本返回 Top-k 预测及置信度。"""
    model.eval()
    with torch.no_grad():
        # model.predict 根据文本是否为空自动选择多模态/纯视觉分支
        if hasattr(model, "predict"):
            logits = model.predict(image.unsqueeze(0).to(device), texts, device=device)
        else:
            logits = model(image.unsqueeze(0).to(device), texts, device=device)[0]
        probs = torch.softmax(logits, dim=1)[0]
        topk = torch.topk(probs, k)
    return [(idx2label[int(idx)], float(prob)) for idx, prob in zip(topk.indices, topk.values)]
