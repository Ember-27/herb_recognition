"""配置加载与工具函数。"""
import os
import random
import numpy as np
import torch
import yaml
from typing import Dict, Any


def load_config(path: str) -> Dict[str, Any]:
    """加载 YAML 配置；命令行参数会覆盖默认值。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def set_seed(seed: int = 42):
    """固定随机种子，保证可复现。"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def get_device(device_str: str) -> torch.device:
    """解析设备字符串，自动回退到 CPU。"""
    if device_str.startswith("cuda") and not torch.cuda.is_available():
        print("[WARN] 请求 CUDA 但不可用，回退到 CPU。")
        return torch.device("cpu")
    return torch.device(device_str)
