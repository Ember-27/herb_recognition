# 官方评估报告（eval_official）

> 本报告由 `eval_official.log` 精炼提取，原始日志留档于同目录，作为审计证据。

## 一、评估概览

| 项目 | 值 |
|------|-----|
| 评估日期 | 2026-08-28 |
| 数据集 | val 集 10000 张，163 类 |
| 权重 | `experiments/checkpoints/best_model.pth`（Epoch 5，510.9 MB） |
| 推理方式 | 有文本走多模态（Swin + BERT + HCA），无文本走纯视觉分支 |
| 原始日志 | `evaluation/reports/eval_official.log`（含 163 类逐类 precision/recall/f1） |

## 二、核心指标

| 模式 | Accuracy | Top-5 |
|------|----------|-------|
| 有文本（多模态） | **0.9995** | **1.0000** |
| 无文本（纯视觉） | **0.9548** | **0.9965** |
| 文本辅助增益 | Δacc = **+0.0447** | — |

## 三、逐类结果摘要

- 163 类中 **159 类 precision / recall / f1 全部 = 1.00**，support 合计 10000。
- macro avg = 1.00，weighted avg = 1.00。
- 非满分类仅 **4 个**，且全部集中在"同药不同形态"的块/片变体互混：

| 类别 | precision | recall | f1 | support | 说明 |
|------|-----------|--------|----|---------|------|
| 首乌藤(shouwutengkuai) | 0.95 | 0.95 | 0.95 | 44 | 块 vs 片互相混淆 |
| 首乌藤(shouwutengpian) | 0.97 | 0.97 | 0.97 | 71 | ↑ 同一对 |
| 天麻(tianmakuai) | 1.00 | 0.98 | 0.99 | 50 | 块 vs 片互相混淆 |
| 天麻(tianmapian) | 0.98 | 1.00 | 0.99 | 60 | ↑ 同一对 |

## 四、结论

1. 模型**几乎不在不同药材之间犯错**，错误全部集中在同种药材的块/片形态变体，属于"人为拆类"的固有难度，而非识别缺陷。
2. 文本辅助增益 Δacc = +0.0447，与 README 记录完全一致，证据链闭合。
3. 演示时可用的故事点：最难的首乌藤块/片（形态相近）模型仍能 95% 区分。

## 五、复现方式

```powershell
venv312\Scripts\python.exe main.py --mode eval --config experiments/configs/default_config.yaml --ckpt experiments/checkpoints/best_model.pth 2>&1 | Tee-Object -FilePath evaluation/reports/eval_official.log
```
