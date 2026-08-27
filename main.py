"""中草药多模态识别系统 - 主入口。

用法:
  python main.py --mode check          # 环境检查 (等价于 check_environment.py)
  python main.py --mode download-data  # 拉取示例数据说明
  python main.py --mode train          # 训练
  python main.py --mode eval           # 评估 (默认同时报有文本/无文本准确率)
  python main.py --mode demo           # 启动 Gradio 演示
  python main.py --mode few-shot       # 小样本原型网络训练+评估
  python main.py --mode few-shot --fs-eval-only  # 仅评估已训练原型网络
  python main.py --mode serve --port 8000  # 启动 REST API 服务 (FastAPI)

可选:
  --config experiments/configs/default_config.yaml
  --ckpt   experiments/checkpoints/best_model.pth
  --llm-config experiments/configs/llm_config.yaml   # /chat 接口的 LLM 配置（可选）
  --no-text                                  评估时仅用纯视觉分支
  --fs-ckpt experiments/checkpoints/few_shot_proto.pth
  --fs-eval-only                             小样本模式只评估不训练

外部 LLM（/chat 接口）:
  API Key 为敏感信息，请手动通过环境变量配置（勿写入代码/配置）:
    PowerShell: $env:ZHIPU_API_KEY="你的key"
    CMD:        setx ZHIPU_API_KEY "你的key"
  可选环境变量: LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT / LLM_TEMPERATURE / LLM_MAX_TOKENS
"""
import os
import sys
import argparse
import torch

# 将项目根目录加入 path，保证 `from models.xxx` 可导入
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.config import load_config, set_seed, get_device
from utils.logger import setup_logging
from loguru import logger


def parse_args():
    p = argparse.ArgumentParser(description="中草药多模态识别系统")
    p.add_argument("--mode", choices=["check", "download-data", "train", "eval", "demo",
                                      "few-shot", "serve"],
                   default="check")
    p.add_argument("--config", default="experiments/configs/default_config.yaml")
    p.add_argument("--ckpt", default=None, help="模型权重路径")
    p.add_argument("--port", type=int, default=8000, help="REST API 服务端口 (serve 模式)")
    p.add_argument("--llm-config", default="experiments/configs/llm_config.yaml",
                   help="外部 LLM 配置 (serve 模式 /chat 接口，可选)")
    p.add_argument("--no-text", action="store_true",
                   help="评估时清空所有文本，仅用纯视觉分支，验证模型是否真会看图")
    p.add_argument("--fs-ckpt", default=None,
                   help="小样本原型网络权重 (默认 experiments/checkpoints/few_shot_proto.pth)")
    p.add_argument("--fs-eval-only", action="store_true",
                   help="小样本模式只评估不训练")
    return p.parse_args()


def check_mode():
    import check_environment
    check_environment.check_python()
    check_environment.check_torch()
    check_environment.check_deps()
    check_environment.check_model_load()


def download_data_mode():
    print("当前为骨架项目，示例数据请按以下方式准备：")
    print("1) 将图片按类别放入 data/raw/<草药名>/ 目录")
    print("2) 生成 data/processed/train.csv，包含列: image_path,label,text")
    print("   示例: data/raw/gouqi/001.jpg,枸杞,味甘性平，归肝、肾经，滋补肝肾")
    print("3) 知识图谱样例已内置: knowledge_graph/herbs_sample.csv")


def train_mode(config):
    from utils.data_utils import HerbDataset, build_label_maps
    from models.classifier import build_classifier
    from training.trainer import Trainer

    set_seed(config.get("seed", 42))
    device = get_device(config["device"])
    label2idx, idx2label = build_label_maps(config["data"]["train_csv"])
    train_ds = HerbDataset(config["data"]["train_csv"], config["data"]["root"],
                           config["image_size"], label2idx, split="train",
                           text_drop_prob=config["training"].get("text_drop_prob", 0.3),
                           text_aug_prob=config["training"].get("text_aug_prob", 0.0))
    val_ds = HerbDataset(config["data"]["val_csv"], config["data"]["root"],
                         config["image_size"], label2idx, split="val")
    train_loader = torch.utils.data.DataLoader(
        train_ds, batch_size=config["batch_size"], shuffle=True,
        num_workers=config["num_workers"], drop_last=True)
    val_loader = torch.utils.data.DataLoader(
        val_ds, batch_size=config["batch_size"], shuffle=False,
        num_workers=config["num_workers"])

    model = build_classifier(config, num_classes=len(label2idx))
    trainer = Trainer(model, train_loader, val_loader, config, device)
    trainer.run()


def eval_mode(config, ckpt, args):
    import torch
    from utils.data_utils import HerbDataset, build_label_maps
    from models.classifier import build_classifier
    from evaluation.evaluator import evaluate

    device = get_device(config["device"])
    label2idx, idx2label = build_label_maps(config["data"]["train_csv"])
    val_ds = HerbDataset(config["data"]["val_csv"], config["data"]["root"],
                         config["image_size"], label2idx, split="val")
    loader = torch.utils.data.DataLoader(
        val_ds, batch_size=config["batch_size"], shuffle=False,
        num_workers=config["num_workers"])
    model = build_classifier(config, num_classes=len(label2idx)).to(device)
    if ckpt and os.path.exists(ckpt):
        model.load_state_dict(torch.load(ckpt, map_location=device))
        logger.info(f"已加载权重: {ckpt}")
    else:
        logger.warning("未指定/未找到权重，使用随机初始化模型评估。")

    if args.no_text:
        # 仅纯视觉分支评估
        res = evaluate(model, loader, device, idx2label, use_text=False)
        print(res["report"])
    else:
        # 同时报告有文本 / 无文本两种模式，对比二者差距
        res_mm = evaluate(model, loader, device, idx2label, use_text=True)
        res_vis = evaluate(model, loader, device, idx2label, use_text=False)
        print(res_mm["report"])
        print("=" * 60)
        print(f"有文本(多模态)  acc={res_mm['accuracy']:.4f}  top5={res_mm['top5']:.4f}")
        print(f"无文本(纯视觉)  acc={res_vis['accuracy']:.4f}  top5={res_vis['top5']:.4f}")
        print(f"文本辅助增益    Δacc={res_mm['accuracy'] - res_vis['accuracy']:+.4f}")
        if res_vis["accuracy"] < 0.5:
            logger.warning("纯视觉 acc 低于 0.5，模型可能仍存在文本泄漏，视觉分支不可靠。")


def demo_mode(config, ckpt):
    from models.classifier import build_classifier
    from utils.data_utils import build_label_maps
    device = get_device(config["device"])
    label2idx, _ = build_label_maps(config["data"]["train_csv"])
    model = build_classifier(config, num_classes=len(label2idx)).to(device)
    # 未显式指定 --ckpt 时，默认加载最佳权重，避免用随机初始化模型演示（置信度≈1/类别数）
    if ckpt is None:
        ckpt = os.path.join(config["training"].get("save_dir", "experiments/checkpoints"),
                            "best_model.pth")
    if ckpt and os.path.exists(ckpt):
        model.load_state_dict(torch.load(ckpt, map_location=device))
        logger.info(f"已加载权重: {ckpt}")
    else:
        logger.warning(f"未找到权重: {ckpt}，使用随机初始化模型演示（请先训练或指定 --ckpt）")
    from app.gradio_app import launch
    launch(config, ckpt, model=model, device=device)


def serve_mode(config, args):
    """启动 REST API 服务（FastAPI），复用 HerbDemo 实现多模态识别/检索/热图。"""
    import uvicorn
    os.environ.setdefault("CONFIG", args.config)
    if args.ckpt:
        os.environ.setdefault("CKPT", args.ckpt)
    if args.llm_config:
        os.environ.setdefault("LLM_CONFIG", args.llm_config)
    from app.api import app
    logger.info(f"REST API 启动于 http://127.0.0.1:{args.port}  (接口: /health /predict /search /explain /chat)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


def few_shot_mode(config, args):
    """小样本学习(原型网络)：以训练好的视觉主干为特征提取器，做 n_way/k_shot 评测。

    默认训练原型网络(仅微调特征投影)；--fs-eval-only 则只评估。
    依赖 --ckpt 指定的已训练分类器权重(用于初始化视觉主干)。
    """
    from utils.data_utils import HerbDataset, build_label_maps
    from models.classifier import build_classifier
    from training.few_shot import (build_feature_extractor, build_prototypical,
                                   FewShotTrainer, _collect_label_map)

    device = get_device(config["device"])
    label2idx, _ = build_label_maps(config["data"]["train_csv"])

    # 1) 载入已训练分类器，复用其视觉主干作为特征提取器
    model = build_classifier(config, num_classes=len(label2idx)).to(device)
    ckpt = args.ckpt or config["training"].get("save_dir", "experiments/checkpoints") + "/best_model.pth"
    if os.path.exists(ckpt):
        model.load_state_dict(torch.load(ckpt, map_location=device))
        logger.info(f"已加载主干权重: {ckpt}")
    else:
        logger.warning("未找到主干权重，使用随机初始化(小样本结果无意义)。")

    # 冻结视觉主干，仅让投影层可学习（避免小样本过拟合主干）
    model.vision.freeze_backbone()

    # 2) 构造原型网络
    extractor = build_feature_extractor(model, config)
    net = build_prototypical(config, extractor).to(device)

    # 3) 从训练集构建 episode 采样所需的 label->index 映射
    train_ds = HerbDataset(config["data"]["train_csv"], config["data"]["root"],
                           config["image_size"], label2idx, split="train",
                           text_drop_prob=0.0)
    label_map = _collect_label_map(train_ds)

    fs_ckpt = args.fs_ckpt or config["training"].get("save_dir", "experiments/checkpoints") + "/few_shot_proto.pth"

    if args.fs_eval_only:
        if os.path.exists(fs_ckpt):
            net.load_state_dict(torch.load(fs_ckpt, map_location=device))
            logger.info(f"已加载小样本权重: {fs_ckpt}")
        else:
            logger.warning("未找到小样本权重，使用当前特征提取器直接评估。")
        trainer = FewShotTrainer(net, train_ds, config, device, label_map)
        trainer.evaluate(n_episodes=int(config["few_shot"].get("eval_episodes", 200)))
        return

    trainer = FewShotTrainer(net, train_ds, config, device, label_map)
    trainer.run()
    # 训练后顺带评估
    trainer.evaluate(n_episodes=int(config["few_shot"].get("eval_episodes", 200)))


def main():
    args = parse_args()
    config = load_config(args.config)
    setup_logging(config["logging"]["level"], config["logging"]["project"])

    if args.mode == "check":
        check_mode()
    elif args.mode == "download-data":
        download_data_mode()
    elif args.mode == "train":
        train_mode(config)
    elif args.mode == "eval":
        eval_mode(config, args.ckpt, args)
    elif args.mode == "demo":
        demo_mode(config, args.ckpt)
    elif args.mode == "few-shot":
        few_shot_mode(config, args)
    elif args.mode == "serve":
        serve_mode(config, args)


if __name__ == "__main__":
    main()
