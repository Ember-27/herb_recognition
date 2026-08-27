"""训练循环：支持主干冻结热身、分层学习率、强正则化(Cutmix/Label Smoothing)。"""
import os
import random
from typing import Dict, Any
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm
from loguru import logger


def cutmix(images: torch.Tensor, labels: torch.Tensor, alpha: float = 1.0):
    """对图像批次做 CutMix，返回混合图像、标签 A、标签 B、lambda。

    文本保持取自原样本 A（主导视角），不随图像混合，避免多模态对不齐。
    """
    lam = random.betavariate(alpha, alpha) if alpha > 0 else 1.0
    batch_size = images.size(0)
    idx = torch.randperm(batch_size, device=images.device)
    y_a, y_b = labels, labels[idx]

    # 在特征图尺度上挖矩形块
    h, w = images.shape[-2], images.shape[-1]
    cut_rat = (1.0 - lam) ** 0.5
    cut_w, cut_h = int(w * cut_rat), int(h * cut_rat)
    cx, cy = random.randint(0, w - 1), random.randint(0, h - 1)
    x1 = max(cx - cut_w // 2, 0)
    y1 = max(cy - cut_h // 2, 0)
    x2 = min(cx + cut_w // 2, w)
    y2 = min(cy + cut_h // 2, h)
    mixed = images.clone()
    mixed[:, :, y1:y2, x1:x2] = images[idx, :, y1:y2, x1:x2]
    # 真实 lambda 以挖块面积占比为准
    lam = 1.0 - ((x2 - x1) * (y2 - y1) / float(w * h))
    return mixed, y_a, y_b, lam


class Trainer:
    def __init__(self, model, train_loader: DataLoader, val_loader: DataLoader,
                 config: Dict[str, Any], device: torch.device):
        self.model = model.to(device)
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.config = config
        self.device = device
        self.epochs = config["training"]["epochs"]
        self.save_dir = config["training"]["save_dir"]
        os.makedirs(self.save_dir, exist_ok=True)

        smoothing = config["training"].get("label_smoothing", 0.0)
        self.criterion = nn.CrossEntropyLoss(label_smoothing=smoothing)
        # 模态平衡损失：用多模态分支的软标签蒸馏纯视觉分支，
        # 强制视觉编码器学会独立分类，避免文本泄漏导致视觉成摆设。
        self.balance_loss = nn.KLDivLoss(reduction="batchmean")
        # 蒸馏温度与权重（可在配置中调整）
        self.balance_temp = float(config["training"].get("balance_temp", 2.0))
        self.balance_weight = float(config["training"].get("balance_weight", 0.5))
        self.optimizer = torch.optim.AdamW(
            model.get_parameter_groups(config["training"]["lr"]),
            weight_decay=config["training"]["weight_decay"],
        )
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, T_max=self.epochs
        )
        self.freeze_epochs = config["vision"].get("freeze_backbone_epochs", 0)
        self.cutmix_prob = config["training"].get("cutmix_prob", 0.0)

    def _maybe_freeze(self, epoch: int):
        if self.freeze_epochs > 0 and epoch < self.freeze_epochs:
            self.model.freeze_vision_backbone()
        else:
            self.model.unfreeze_vision_backbone()

    def _branch_losses(self, images, texts, labels, use_cutmix, y_a, y_b, lam):
        """计算多模态分支 + 纯视觉分支的联合损失，返回 (loss, logits)。"""
        mm_logits, vis_logits = self.model(images, texts, device=self.device)

        if use_cutmix:
            loss_mm = lam * self.criterion(mm_logits, y_a) + \
                      (1 - lam) * self.criterion(mm_logits, y_b)
            loss_vis = lam * self.criterion(vis_logits, y_a) + \
                       (1 - lam) * self.criterion(vis_logits, y_b)
        else:
            loss_mm = self.criterion(mm_logits, labels)
            loss_vis = self.criterion(vis_logits, labels)
            # 模态平衡：纯视觉分支向多模态分支的软化分布对齐
            if self.balance_weight > 0:
                with torch.no_grad():
                    mm_soft = torch.log_softmax(mm_logits / self.balance_temp, dim=1)
                vis_log = torch.log_softmax(vis_logits / self.balance_temp, dim=1)
                loss_vis = loss_vis + self.balance_weight * self.balance_temp ** 2 * \
                    self.balance_loss(vis_log, mm_soft.exp())

        loss = loss_mm + loss_vis
        return loss, mm_logits

    def train_one_epoch(self, epoch: int):
        self.model.train()
        self._maybe_freeze(epoch)
        total_loss, correct, total = 0.0, 0, 0
        for batch in tqdm(self.train_loader, desc=f"Epoch {epoch+1} train"):
            images = batch["image"].to(self.device)
            texts = batch["text"]
            labels = batch["label"].to(self.device)

            self.optimizer.zero_grad()
            use_cutmix = self.cutmix_prob > 0 and random.random() < self.cutmix_prob
            if use_cutmix:
                images, y_a, y_b, lam = cutmix(images, labels)
                loss, logits = self._branch_losses(
                    images, texts, labels, True, y_a, y_b, lam)
            else:
                loss, logits = self._branch_losses(
                    images, texts, labels, False, None, None, 1.0)
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item() * images.size(0)
            correct += (logits.argmax(1) == labels).sum().item()
            total += images.size(0)

        avg_loss = total_loss / max(total, 1)
        acc = correct / max(total, 1)
        logger.info(f"Epoch {epoch+1} 训练 loss={avg_loss:.4f} acc={acc:.4f}")
        return avg_loss, acc

    @torch.no_grad()
    def evaluate(self):
        """返回 (有文本 acc, 无文本 acc) 元组，验证视觉分支是否真的会看图。"""
        self.model.eval()
        correct_mm, correct_vis, total = 0, 0, 0
        for batch in tqdm(self.val_loader, desc="val"):
            images = batch["image"].to(self.device)
            texts = batch["text"]
            labels = batch["label"].to(self.device)
            mm_logits, vis_logits = self.model(images, texts, device=self.device)
            correct_mm += (mm_logits.argmax(1) == labels).sum().item()
            correct_vis += (vis_logits.argmax(1) == labels).sum().item()
            total += images.size(0)
        acc_mm = correct_mm / max(total, 1)
        acc_vis = correct_vis / max(total, 1)
        return acc_mm, acc_vis

    def run(self):
        best_acc = 0.0
        # 纯视觉分支最低保底线：低于此值说明视觉退化成摆设，不保存该模型
        min_vision_acc = float(self.config["training"].get("min_vision_acc", 0.0))
        # 早停：连续 patience 个 epoch 有文本 val_acc 无提升则停止
        patience = int(self.config["training"].get("early_stopping_patience", 5))
        min_delta = float(self.config["training"].get("early_stopping_min_delta", 1e-4))
        epochs_no_improve = 0
        for epoch in range(self.epochs):
            self.train_one_epoch(epoch)
            acc_mm, acc_vis = self.evaluate()
            logger.info(f"Epoch {epoch+1} 验证 acc(有文本)={acc_mm:.4f} "
                        f"acc(无文本/纯视觉)={acc_vis:.4f}")
            self.scheduler.step()
            improved = acc_mm > best_acc + min_delta and acc_vis >= min_vision_acc
            if improved:
                best_acc = acc_mm
                epochs_no_improve = 0
                torch.save(self.model.state_dict(),
                           os.path.join(self.save_dir, "best_model.pth"))
                logger.info(f"保存最佳模型，val_acc(有文本)={best_acc:.4f} "
                            f"纯视觉={acc_vis:.4f}")
            else:
                epochs_no_improve += 1
                logger.info(f"验证集未提升 ({epochs_no_improve}/{patience})")
                if epochs_no_improve >= patience:
                    logger.info(f"早停触发：连续 {patience} 个 epoch 无提升，"
                                f"停止训练。最佳 val_acc(有文本)={best_acc:.4f}")
                    break
        else:
            logger.info(f"训练完成(跑满 {self.epochs} 轮)，"
                        f"最佳验证 acc(有文本)={best_acc:.4f}")
        logger.info(f"训练结束，最佳验证 acc(有文本)={best_acc:.4f}")
