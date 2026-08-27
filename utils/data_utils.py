"""数据集与数据增强工具。"""
import os
import random
import numpy as np
import pandas as pd
from torch.utils.data import Dataset
from PIL import Image
import albumentations as A
from albumentations.pytorch import ToTensorV2


class HerbDataset(Dataset):
    """多模态中草药数据集。

    CSV 列: image_path, label, text
    - image_path: 相对于 data.root 的图片路径
    - label: 类别名（字符串，内部映射到整数）
    - text: 该草药的药性/描述文本
    """

    def __init__(self, csv_path: str, data_root: str, image_size: int = 224,
                 label2idx: dict = None, split: str = "train",
                 text_drop_prob: float = 0.0, text_aug_prob: float = 0.0):
        self.data_root = data_root
        self.image_size = image_size
        self.split = split
        # 训练时随机丢弃文本的概率，迫使模型学会"纯视觉分类"。
        # 验证/测试阶段固定为 0（保留真实文本），保证评估反映辅助文本价值。
        self.text_drop_prob = text_drop_prob if split == "train" else 0.0
        # 训练时以一定概率把"完整药性描述"替换为"仅功效片段"
        # （如从"功效：滋补肝肾、益精明目"截取"滋补肝肾"），
        # 模拟用户只输入功效短语的场景，避免推理时因文本形态差异被拉偏。
        self.text_aug_prob = text_aug_prob if split == "train" else 0.0
        self.df = pd.read_csv(csv_path)
        if label2idx is None:
            classes = sorted(self.df["label"].unique().tolist())
            self.label2idx = {c: i for i, c in enumerate(classes)}
        else:
            self.label2idx = label2idx
        self.idx2label = {v: k for k, v in self.label2idx.items()}
        self.transform = self._build_transform()

    def _build_transform(self):
        if self.split == "train":
            # 强数据增强：迫使模型学习草药本身的形态，而非训练集特定的
            # 拍摄背景/打光风格，从而提升对真实场景图片的泛化能力。
            return A.Compose([
                # 模拟不同远近/取景：随机裁剪后缩放，破坏"固定背景"相关性
                A.RandomResizedCrop(
                    size=(self.image_size, self.image_size),
                    scale=(0.55, 1.0), ratio=(0.75, 1.33), p=1.0),
                A.HorizontalFlip(p=0.5),
                A.VerticalFlip(p=0.2),
                # 大幅色彩/亮度扰动，模拟不同光照与设备
                A.ColorJitter(brightness=0.4, contrast=0.4,
                              saturation=0.4, hue=0.1, p=0.8),
                A.HueSaturationValue(hue_shift_limit=20, sat_shift_limit=30,
                                     val_shift_limit=20, p=0.5),
                A.RandomBrightnessContrast(brightness_limit=0.3,
                                           contrast_limit=0.3, p=0.5),
                # 更大尺度的几何变换
                A.ShiftScaleRotate(shift_limit=0.2, scale_limit=0.2,
                                   rotate_limit=30, p=0.5),
                A.RandomRotate90(p=0.3),
                # 模糊/噪声：模拟真实拍摄的画质差异
                A.OneOf([
                    A.GaussianBlur(blur_limit=(3, 7), p=1.0),
                    A.MotionBlur(blur_limit=5, p=1.0),
                ], p=0.3),
                A.GaussNoise(std_range=(0.05, 0.15), p=0.3),
                # 遮挡/丢块：迫使模型关注全局形态而非局部纹理
                # albumentations 2.x 用 fill 且范围按像素或比例 (此处用像素)
                A.CoarseDropout(num_holes_range=(1, 8), hole_height_range=(8, 32),
                                hole_width_range=(8, 32),
                                fill=list((np.array([0.485, 0.456, 0.406])
                                           * 255).astype(int)), p=0.4),
                A.Normalize(mean=(0.485, 0.456, 0.406),
                           std=(0.229, 0.224, 0.225)),
                ToTensorV2(),
            ])
        # 验证/测试：仅做确定性预处理，不做随机增强
        return A.Compose([
            A.Resize(self.image_size, self.image_size),
            A.Normalize(mean=(0.485, 0.456, 0.406),
                       std=(0.229, 0.224, 0.225)),
            ToTensorV2(),
        ])

    @staticmethod
    def _extract_function(text: str):
        """从药性描述里截取"功效：..."后的功效片段，用于文本增强。

        例如 "性味甘平；归肝肾经；功效：滋补肝肾、益精明目。" -> "滋补肝肾、益精明目"
        找不到则返回空字符串。
        """
        import re
        m = re.search(r"功效[:：]\s*([^。；;]+)", text)
        if m:
            return m.group(1).strip()
        return ""

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        img_path = os.path.join(self.data_root, row["image_path"])
        image = Image.open(img_path).convert("RGB")
        image = self.transform(image=np.array(image))["image"]
        label = self.label2idx[row["label"]]
        if self.text_drop_prob > 0 and random.random() < self.text_drop_prob:
            text = ""  # 模态丢弃：本样本本轮不提供文本，强制纯视觉学习
        else:
            text = str(row["text"]) if "text" in row and pd.notna(row["text"]) else ""
            # 功效片段增强：随机截取"功效：..."后的片段作为更短的辅助文本
            if self.text_aug_prob > 0 and text and random.random() < self.text_aug_prob:
                func = self._extract_function(text)
                if func:
                    text = func
        return {
            "image": image,
            "text": text,
            "label": label,
            "label_name": row["label"],
        }


def build_label_maps(train_csv: str):
    """从训练集 CSV 构建 label2idx / idx2label。"""
    import pandas as pd
    df = pd.read_csv(train_csv)
    classes = sorted(df["label"].unique().tolist())
    label2idx = {c: i for i, c in enumerate(classes)}
    return label2idx, {v: k for k, v in label2idx.items()}
