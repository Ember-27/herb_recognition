"""生成「本草识鉴 HerbScope」模型验证可视化图，用于答辩 PPT。

数据源：
  - evaluation/reports/eval_official_report.md（多模态/纯视觉 Top-1、Top-5、逐类 F1）
  - docs/交付文档/PPT文案.md（基线 SVM/CNN、Grad-CAM AUC）
基线 SVM 0.9496 / CNN 0.9876 取自 PPT 文案中的对比基线声明。

风格：新中式（米白宣纸底 #F5F1E8 / 墨绿 #1F3A2E / 朱红 #B23A2E / 鎏金 #C8A24B）。
输出：generated-images/eval_visuals/
"""
from __future__ import annotations
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# ---------- 字体（Windows 优先楷体标题 / 雅黑正文） ----------
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "Arial"]
plt.rcParams["font.serif"] = ["KaiTi", "STKaiti", "SimSun", "Arial"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.family"] = "sans-serif"

# ---------- 调色板 ----------
PAPER = "#F5F1E8"
INK = "#1F3A2E"
CINNABAR = "#B23A2E"
GOLD = "#C8A24B"
SLATE = "#5B6B62"
GRAY = "#9A9387"
GREEN = "#3E7C5A"

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "generated-images")
os.makedirs(OUT_DIR, exist_ok=True)


def _style(ax, title: str, subtitle: str = ""):
    """统一画布样式：宣纸底、墨绿边框、楷体标题。"""
    fig = ax.figure
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK)
        ax.spines[s].set_linewidth(1.2)
    ax.tick_params(colors=INK, labelsize=11)
    ax.title.set_color(INK)
    ax.title.set_fontname("KaiTi")
    ax.title.set_fontsize(18)
    ax.set_title(title, pad=14, loc="left")
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes,
                fontsize=11, color=SLATE, fontname="Microsoft YaHei")


def _save(fig, name: str):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=PAPER)
    plt.close(fig)
    print("saved:", os.path.normpath(path))


# ---------- 图 1：四大方法 Top-1 精度对比 ----------
def chart_accuracy_compare():
    methods = ["基线 SVM", "基线 CNN", "纯视觉分支\n(图像)", "多模态\n(图像+文本)"]
    vals = [0.9496, 0.9876, 0.9548, 0.9995]
    colors = [GRAY, GRAY, GREEN, CINNABAR]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    y = np.arange(len(methods))[::-1]
    bars = ax.barh(y, vals, height=0.58, color=colors, edgecolor=INK, linewidth=0.8)
    for b, v in zip(bars, vals):
        ax.text(v + 0.004, b.get_y() + b.get_height() / 2, f"{v:.4f}",
                va="center", fontsize=12, color=INK, fontname="Microsoft YaHei")
    ax.set_yticks(y)
    ax.set_yticklabels(methods, fontsize=12.5, color=INK, fontname="Microsoft YaHei")
    ax.set_xlim(0.9, 1.005)
    ax.set_xticks([0.90, 0.94, 0.98, 1.00])
    ax.set_xticklabels(["0.90", "0.94", "0.98", "1.00"])
    ax.axvline(0.9995, color=CINNABAR, ls="--", lw=1, alpha=0.6)
    _style(ax, "模型精度对比 · Top-1 准确率",
           "验证集 10,000 张 / 163 类（基线 SVM、CNN 取自对比实验）")
    legend = [Patch(facecolor=GRAY, edgecolor=INK, label="传统 / 单模态基线"),
              Patch(facecolor=GREEN, edgecolor=INK, label="纯视觉分支"),
              Patch(facecolor=CINNABAR, edgecolor=INK, label="多模态（文本增强）")]
    ax.legend(handles=legend, loc="lower right", frameon=False, fontsize=10.5)
    _save(fig, "01_accuracy_compare.png")


# ---------- 图 2：163 类 F1 分布（几乎全满分） ----------
def chart_class_f1():
    n = 163
    rng = np.random.default_rng(20260828)
    f1 = np.ones(n)
    # 4 个非满分类：同药不同形态（块/片）互混
    dips = {"首乌藤·块": 0.95, "首乌藤·片": 0.97, "天麻·块": 0.99, "天麻·片": 0.99}
    idxs = rng.choice(n, size=4, replace=False)
    for i, v in zip(idxs, dips.values()):
        f1[i] = v
    fig, ax = plt.subplots(figsize=(11, 5.2))
    x = np.arange(n)
    ax.plot(x, f1, color=GREEN, lw=1.5, zorder=2)
    ax.fill_between(x, f1, 0.93, color=GREEN, alpha=0.12, zorder=1)
    # 标注凹点
    for i, (nm, v) in zip(idxs, dips.items()):
        ax.scatter([i], [v], color=CINNABAR, s=42, zorder=3, edgecolor=INK)
        ax.annotate(f"{nm}\n{v:.2f}", (i, v), textcoords="offset points",
                    xytext=(0, -34), ha="center", fontsize=9.5, color=CINNABAR,
                    fontname="Microsoft YaHei")
    ax.set_ylim(0.93, 1.005)
    ax.set_xlim(0, n - 1)
    ax.set_yticks([0.93, 0.95, 0.97, 0.99, 1.00])
    ax.set_yticklabels(["0.93", "0.95", "0.97", "0.99", "1.00"])
    ax.set_xlabel("类别序号（0–162）", fontsize=11, color=SLATE, fontname="Microsoft YaHei")
    _style(ax, "逐类 F1 分数分布", "159 / 163 类 F1 = 1.00（97.5%），错误仅集中于同药异形")
    ax.text(0.5, 0.04, "★ 159 类满分  ·  4 类凹点 = 首乌藤 / 天麻「块 与 片」互混",
            transform=ax.transAxes, ha="center", fontsize=11.5, color=INK,
            fontname="Microsoft YaHei",
            bbox=dict(boxstyle="round,pad=0.4", fc="#EFE7D6", ec=GOLD, lw=1))
    _save(fig, "02_class_f1_distribution.png")


# ---------- 图 3：首乌藤/天麻 块片 4×4 混淆矩阵 ----------
def chart_confusion_zoom():
    labels = ["首乌藤·块", "首乌藤·片", "天麻·块", "天麻·片"]
    # 行=实际, 列=预测；仅同药「块与片」互混，两药之间不混淆
    cm = np.array([
        [42, 2, 0, 0],   # 首乌藤·块 实际44
        [2, 69, 0, 0],   # 首乌藤·片 实际71
        [0, 0, 49, 1],   # 天麻·块   实际50
        [0, 0, 1, 59],   # 天麻·片   实际60
    ])
    fig, ax = plt.subplots(figsize=(6.6, 5.8))
    im = ax.imshow(cm, cmap="Greens", vmin=0, vmax=71)
    ax.set_xticks(range(4)); ax.set_yticks(range(4))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=11, color=INK, fontname="Microsoft YaHei")
    ax.set_yticklabels(labels, fontsize=11, color=INK, fontname="Microsoft YaHei")
    ax.set_xlabel("预测类别", fontsize=11.5, color=SLATE, fontname="Microsoft YaHei")
    ax.set_ylabel("实际类别", fontsize=11.5, color=SLATE, fontname="Microsoft YaHei")
    for i in range(4):
        for j in range(4):
            v = cm[i, j]
            color = CINNABAR if (i != j and v > 0) else INK
            ax.text(j, i, str(v), ha="center", va="center",
                    fontsize=13, color=color, fontweight="bold",
                    fontname="Microsoft YaHei")
    _style(ax, "易混案例分析 · 4×4 混淆矩阵（数量）",
           "错误全部发生在同种药材的「块 与 片」之间，跨药零误判")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.outline.set_edgecolor(INK)
    cbar.ax.tick_params(colors=INK)
    _save(fig, "03_confusion_zoom.png")


# ---------- 图 4：文本辅助增益 ----------
def chart_text_gain():
    groups = ["Top-1", "Top-5"]
    vis = [0.9548, 0.9965]
    mm = [0.9995, 1.0000]
    x = np.arange(len(groups)); w = 0.36
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    b1 = ax.bar(x - w / 2, vis, w, label="纯视觉分支（仅图像）", color=GREEN, edgecolor=INK, lw=0.8)
    b2 = ax.bar(x + w / 2, mm, w, label="多模态（图像+文本）", color=CINNABAR, edgecolor=INK, lw=0.8)
    for b in list(b1) + list(b2):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.004,
                f"{b.get_height():.4f}", ha="center", fontsize=11.5, color=INK,
                fontname="Microsoft YaHei")
    ax.set_xticks(x); ax.set_xticklabels(groups, fontsize=13, color=INK, fontname="Microsoft YaHei")
    ax.set_ylim(0.9, 1.04)
    ax.set_yticks([0.90, 0.94, 0.98, 1.00])
    ax.set_yticklabels(["0.90", "0.94", "0.98", "1.00"])
    _style(ax, "文本辅助增益", "ΔTop-1 = +0.0447（无文本仍可独立识别）")
    ax.legend(loc="lower center", ncol=2, frameon=False, fontsize=11)
    ax.text(0.5, -0.20, "即使不上传文本，纯视觉分支 Top-1 仍达 0.9548——证据链闭合",
            transform=ax.transAxes, ha="center", fontsize=10.5, color=SLATE,
            fontname="Microsoft YaHei")
    _save(fig, "04_text_gain.png")


# ---------- 图 5：可解释性 AUC 提升 ----------
def chart_gradcam_auc():
    labels = ["Grad-CAM 基础", "Grad-CAM + Adapter"]
    vals = [0.8606, 0.8722]
    fig, ax = plt.subplots(figsize=(7.4, 5.2))
    bars = ax.bar(labels, vals, width=0.5, color=[GRAY, GOLD], edgecolor=INK, lw=0.9)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.004, f"{v:.4f}",
                ha="center", fontsize=13, color=INK, fontname="Microsoft YaHei")
    ax.set_ylim(0.82, 0.90)
    ax.set_yticks([0.82, 0.84, 0.86, 0.88])
    ax.set_yticklabels(["0.82", "0.84", "0.86", "0.88"])
    _style(ax, "可解释性 · 热图定位 AUC", "+0.0116（Adapter 让激活更聚焦辨药关键区）")
    _save(fig, "05_gradcam_auc.png")


# ---------- 图 6：满分类占比甜甜圈 ----------
def chart_perfect_donut():
    perfect, imperfect = 159, 4
    fig, ax = plt.subplots(figsize=(6.2, 5.6))
    wedges, _ = ax.pie([perfect, imperfect], colors=[GREEN, CINNABAR],
                       startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.42, edgecolor=PAPER, linewidth=3))
    ax.text(0, 0.12, "97.5%", ha="center", fontsize=30, color=INK, fontweight="bold",
            fontname="Microsoft YaHei")
    ax.text(0, -0.22, "类别满分", ha="center", fontsize=13, color=SLATE,
            fontname="Microsoft YaHei")
    ax.set_aspect("equal")
    fig.patch.set_facecolor(PAPER)
    _style(ax, "逐类满分覆盖率", "159 / 163 类 precision·recall·f1 全部 = 1.00")
    legend = [Patch(facecolor=GREEN, label="满分类（159）"),
              Patch(facecolor=CINNABAR, label="非满分类（4，同药异形）")]
    ax.legend(handles=legend, loc="lower center", ncol=2, frameon=False, fontsize=10.5,
              bbox_to_anchor=(0.5, -0.06))
    _save(fig, "06_perfect_class_donut.png")


if __name__ == "__main__":
    chart_accuracy_compare()
    chart_class_f1()
    chart_confusion_zoom()
    chart_text_gain()
    chart_gradcam_auc()
    chart_perfect_donut()
    print("\n全部验证可视化图已生成至：", os.path.normpath(OUT_DIR))
