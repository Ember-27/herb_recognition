"""小样本学习：Prototypical Network (原型网络)。

用于中草药长尾/稀缺类别的少样本分类。给定 support 集计算每类原型，
query 样本按到各原型的距离分类。特征由已训练的视觉编码器(或 HerbClassifier
的视觉分支)提取，避免依赖文本造成泄漏。

用法 (命令行):
  python main.py --mode few-shot --ckpt experiments/checkpoints/best_model.pth
  # 评估 n_way=5, k_shot=3 的少样本准确率

设计要点:
  - encoder 统一为「(images, texts) -> [B, D]」接口，并内部把 HerbClassifier
    的双输出 forward 适配为仅取视觉特征，保证小样本走纯视觉分支。
  - 支持 episodic 训练：每个 episode 随机抽 n_way 类、每类 k_shot 个 support +
    q_query 个 query。
"""
from typing import Dict, Any, List, Tuple
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from models.vision_encoder import build_vision_encoder


class VisionFeatureExtractor(nn.Module):
    """把任意视觉编码器包装成 (images, texts) -> [B, D] 的纯视觉特征提取器。

    - 若传入 HerbClassifier，则复用其 vision 主干 + proj（忽略文本，纯视觉）。
    - 若传入裸 VisionEncoder，则直接 forward。
    这样 PrototypicalNet 不必关心上游是分类器还是编码器。
    """

    def __init__(self, backbone, embed_dim: int):
        super().__init__()
        self.backbone = backbone
        self.embed_dim = embed_dim

    def forward(self, images: torch.Tensor, texts=None, device=None):
        device = device or next(self.backbone.parameters()).device
        images = images.to(device)
        # HerbClassifier: 直接取 vision 主干特征（不进 fusion/head，避免文本泄漏）
        if hasattr(self.backbone, "vision") and isinstance(self.backbone.vision, nn.Module):
            return self.backbone.vision(images)
        # 裸 VisionEncoder
        if hasattr(self.backbone, "extract_features"):
            return self.backbone.extract_features(images)
        return self.backbone(images)


class PrototypicalNet(nn.Module):
    def __init__(self, encoder: nn.Module, embed_dim: int):
        """encoder: 输出 [B, embed_dim] 特征的视觉编码器(或适配层)。"""
        super().__init__()
        self.encoder = encoder
        self.embed_dim = embed_dim

    def forward(self, support_x, support_y, query_x):
        """
        support_x: [N_support, 3, H, W] 图像张量
        support_y: [N_support] 类别索引
        query_x:   [N_query, 3, H, W]
        返回 query 的 logits [N_query, n_way]
        """
        s_feat = self.encoder(support_x)         # [N_support, D]
        q_feat = self.encoder(query_x)           # [N_query, D]
        n_way = int(support_y.max().item()) + 1
        prototypes = []
        for c in range(n_way):
            mask = (support_y == c)
            if mask.sum() == 0:
                # 该类无 support 样本，用零向量占位（不应发生）
                prototypes.append(torch.zeros(1, s_feat.size(1), device=s_feat.device))
            else:
                prototypes.append(s_feat[mask].mean(0, keepdim=True))
        prototypes = torch.cat(prototypes, dim=0)          # [n_way, D]
        dist = torch.cdist(q_feat, prototypes)             # [N_query, n_way]
        return -dist

    def embed(self, x: torch.Tensor, device=None):
        return self.encoder(x, device=device)


def proto_loss(logits, query_y):
    return F.cross_entropy(logits, query_y)


def build_prototypical(config: Dict[str, Any], encoder: nn.Module) -> PrototypicalNet:
    return PrototypicalNet(encoder, config["fusion"]["embed_dim"])


# ---------------------------------------------------------------------------
# Episodic 采样：从带 label 的数据集按 n_way / k_shot 抽取 episode
# ---------------------------------------------------------------------------
class EpisodicSampler:
    """给定一个按类别分组的样本索引字典，迭代产生 (support, query) 批次。"""

    def __init__(self, label_to_indices: Dict[int, List[int]], n_way: int,
                 k_shot: int, q_query: int, episodes: int = 100, seed: int = 0):
        self.label_to_indices = {k: v for k, v in label_to_indices.items() if len(v) >= k_shot}
        self.n_way = n_way
        self.k_shot = k_shot
        self.q_query = q_query
        self.episodes = episodes
        self.rng = random.Random(seed)

    def __len__(self):
        return self.episodes

    def __iter__(self):
        classes = list(self.label_to_indices.keys())
        for _ in range(self.episodes):
            chosen = self.rng.sample(classes, self.n_way)
            support_idx, query_idx, support_y, query_y = [], [], [], []
            for new_c, c in enumerate(chosen):
                idxs = self.rng.sample(self.label_to_indices[c], self.k_shot + self.q_query)
                support_idx.extend(idxs[: self.k_shot])
                query_idx.extend(idxs[self.k_shot: self.k_shot + self.q_query])
                support_y.extend([new_c] * self.k_shot)
                query_y.extend([new_c] * self.q_query)
            yield (support_idx, query_idx, support_y, query_y)


def _collect_label_map(dataset) -> Dict[int, List[int]]:
    """从 HerbDataset 收集 label -> 样本下标。"""
    mapping: Dict[int, List[int]] = {}
    for i in range(len(dataset)):
        y = int(dataset[i]["label"])
        mapping.setdefault(y, []).append(i)
    return mapping


class FewShotTrainer:
    """原型网络训练器：冻结主干，仅训练投影/特征对齐。"""

    def __init__(self, net: PrototypicalNet, dataset, config: Dict[str, Any],
                 device: torch.device, label_map: Dict[int, List[int]] = None):
        self.net = net.to(device)
        self.dataset = dataset
        self.config = config
        self.device = device
        fs = config["few_shot"]
        self.n_way = int(fs.get("n_way", 5))
        self.k_shot = int(fs.get("k_shot", 3))
        self.q_query = int(fs.get("q_query", 5))
        self.episodes = int(fs.get("episodes_per_epoch", 100))
        self.epochs = int(fs.get("epochs", 10))
        self.save_dir = config["training"]["save_dir"]
        self.label_map = label_map or _collect_label_map(dataset)
        self.optimizer = torch.optim.AdamW(
            [p for p in self.net.encoder.parameters() if p.requires_grad]
            or list(self.net.parameters()),
            lr=float(fs.get("lr", 1e-4)),
        )

    def _build_episode(self, sampler: EpisodicSampler):
        support_idx, query_idx, support_y, query_y = next(iter(sampler))
        support_x = torch.stack([self.dataset[i]["image"] for i in support_idx]).to(self.device)
        query_x = torch.stack([self.dataset[i]["image"] for i in query_idx]).to(self.device)
        support_y = torch.tensor(support_y, device=self.device)
        query_y = torch.tensor(query_y, device=self.device)
        return support_x, query_x, support_y, query_y

    def run(self):
        best_acc = 0.0
        for epoch in range(self.epochs):
            self.net.train()
            sampler = EpisodicSampler(self.label_map, self.n_way, self.k_shot,
                                      self.q_query, episodes=self.episodes, seed=epoch)
            total_loss, correct, total = 0.0, 0, 0
            for (support_x, query_x, support_y, query_y) in sampler:
                self.optimizer.zero_grad()
                logits = self.net(support_x, support_y, query_x)
                loss = proto_loss(logits, query_y)
                loss.backward()
                self.optimizer.step()
                total_loss += loss.item()
                correct += (logits.argmax(1) == query_y).sum().item()
                total += query_y.size(0)
            acc = correct / total
            print(f"[few-shot] Epoch {epoch+1} loss={total_loss/len(sampler):.4f} "
                  f"acc={acc:.4f}")
            if acc > best_acc:
                best_acc = acc
                torch.save(self.net.state_dict(),
                           f"{self.save_dir}/few_shot_proto.pth")
        print(f"[few-shot] 训练完成，最佳 acc={best_acc:.4f}")

    @torch.no_grad()
    def evaluate(self, n_episodes: int = 200, seed: int = 42):
        """在随机 episode 上评估少样本准确率。"""
        self.net.eval()
        sampler = EpisodicSampler(self.label_map, self.n_way, self.k_shot,
                                  self.q_query, episodes=n_episodes, seed=seed)
        correct, total = 0, 0
        for (support_x, query_x, support_y, query_y) in sampler:
            logits = self.net(support_x, support_y, query_x)
            correct += (logits.argmax(1) == query_y).sum().item()
            total += query_y.size(0)
        acc = correct / total
        print(f"[few-shot] 评估 n_way={self.n_way} k_shot={self.k_shot} "
              f"acc={acc:.4f} (n_episodes={n_episodes})")
        return acc


def build_feature_extractor(backbone, config: Dict[str, Any]) -> VisionFeatureExtractor:
    return VisionFeatureExtractor(backbone, config["fusion"]["embed_dim"])


if __name__ == "__main__":
    # 自测：随机特征 + 裸编码器
    enc = build_vision_encoder({"vision": {"name": "swin_tiny"}, "fusion": {"embed_dim": 512}})
    extractor = VisionFeatureExtractor(enc, 512)
    net = PrototypicalNet(extractor, 512)
    s_x = torch.randn(10, 3, 224, 224)
    s_y = torch.tensor([0] * 5 + [1] * 5)
    q_x = torch.randn(4, 3, 224, 224)
    logits = net(s_x, s_y, q_x)
    print("原型网络 logits 形状:", tuple(logits.shape))   # (4, 2)
