"""环境检查脚本。

用法:
  python main.py --mode check     # 等价
  python check_environment.py

逐项检查 Python / PyTorch / 依赖 / 模型加载，任一项失败返回非零退出码。
"""
import importlib.util
import os
import platform
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# 关键依赖：requirements.txt 中需单独探测的包名 -> 可 import 的模块名
KEY_DEPS = [
    ("torch", "torch"),
    ("torchvision", "torchvision"),
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("timm", "timm"),
    ("albumentations", "albumentations"),
    ("gradio", "gradio"),
    ("networkx", "networkx"),
    ("scikit-learn", "sklearn"),
    ("transformers", "transformers"),
    ("loguru", "loguru"),
    ("PyYAML", "yaml"),
    ("matplotlib", "matplotlib"),
    ("Pillow", "PIL"),
]


def check_python() -> bool:
    v = sys.version_info
    print(f"[Python] {platform.python_version()} (要求 >= 3.9)")
    if v < (3, 9):
        print("  FAIL: 请升级 Python >= 3.9")
        return False
    print("  PASS")
    return True


def check_torch() -> bool:
    try:
        import torch
    except ImportError:
        print("[PyTorch] FAIL: 未安装 torch，请执行: pip install -r requirements.txt")
        return False
    cuda = torch.cuda.is_available()
    print(f"[PyTorch] {torch.__version__} | CUDA 可用: {cuda}")
    if cuda:
        print(f"  GPU: {torch.cuda.get_device_name(0)} "
              f"({torch.cuda.get_device_properties(0).total_memory / 1024 ** 3:.1f} GB)")
    else:
        print("  WARN: 未检测到 GPU，训练/推理将回退到 CPU（速度较慢）")
    return True


def check_deps() -> bool:
    missing = []
    for pkg, mod in KEY_DEPS:
        if importlib.util.find_spec(mod) is None:
            missing.append(pkg)
    if missing:
        print(f"[Deps] FAIL: 缺失依赖 -> {', '.join(missing)}")
        print("       请执行: pip install -r requirements.txt")
        return False
    print("[Deps] PASS: 全部关键依赖已安装")
    return True


def check_model_load() -> bool:
    try:
        import torch
        from utils.config import load_config, get_device
        from models.classifier import build_classifier
        from utils.data_utils import build_label_maps
    except Exception as e:  # noqa: BLE001
        print(f"[Model] FAIL: 导入失败 -> {e}")
        return False
    try:
        cfg = load_config(os.path.join(ROOT, "experiments/configs/default_config.yaml"))
        device = get_device(cfg["device"])
        label2idx, _ = build_label_maps(cfg["data"]["train_csv"])
        model = build_classifier(cfg, num_classes=len(label2idx)).to(device)
        n_params = sum(p.numel() for p in model.parameters()) / 1e6
        print(f"[Model] PASS: 模型构建成功 (类别数={len(label2idx)}, 参数量={n_params:.1f}M)")
        ckpt = os.path.join(cfg["training"].get("save_dir", "experiments/checkpoints"),
                            "best_model.pth")
        if os.path.exists(ckpt):
            model.load_state_dict(torch.load(ckpt, map_location=device))
            print(f"  PASS: 权重加载成功 ({os.path.getsize(ckpt) / 1e6:.1f} MB)")
        else:
            print(f"  WARN: 未找到权重 {ckpt}，跳过权重加载（推理前请先训练）")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Model] FAIL: {e}")
        return False


def main() -> int:
    print("=" * 56)
    print("中草药多模态识别系统 - 环境检查")
    print("=" * 56)
    results = [
        check_python(),
        check_torch(),
        check_deps(),
        check_model_load(),
    ]
    print("=" * 56)
    if all(results):
        print("结论: 环境就绪，可以开始训练/演示。")
        return 0
    print("结论: 存在环境问题，请按上方 FAIL 提示修复。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
