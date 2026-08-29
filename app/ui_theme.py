"""新中式（新国风）主题模块 —— 宣纸米白 + 墨 + 朱砂

设计依据：UI/UX Pro Max 工具建议
- 产品类型：Healthcare App -> 柔和 UI + 可及性优先
- 风格：E-Ink / Paper（纸感、高对比、阅读友好，WCAG AAA）
- 配色：用户选定「米白 + 墨 + 朱砂」（传统宣纸底 + 墨黑文字 + 朱砂红点缀）
- 字体：标题衬线（宋体系）+ 正文无衬线（黑体系）
- 可及性：警示信息一律「图标 + 文字」双重传达，不只用颜色
"""
from __future__ import annotations

from gradio.themes.utils.colors import Color

# ---------------------------------------------------------------------------
# 自定义色相
# ---------------------------------------------------------------------------

VERMILION = Color(  # 朱砂红（主色/强调）
    name="vermilion",
    c50="#FDF5F3",
    c100="#FBE9E4",
    c200="#F6D0C6",
    c300="#EFAC9C",
    c400="#E6816B",
    c500="#D95A3F",
    c600="#C13B27",
    c700="#A93226",  # 章红/警示
    c800="#8C2A1F",
    c900="#73231A",
    c950="#5E1D15",
)

INK = Color(  # 墨色（中性）
    name="ink",
    c50="#FAF8F3",  # 宣纸白
    c100="#F3F0E6",
    c200="#E5DFD0",
    c300="#D3CCB6",
    c400="#B5AD94",
    c500="#968D75",
    c600="#6E675E",  # 淡墨
    c700="#4A4640",
    c800="#33312C",
    c900="#26241F",
    c950="#1A1915",  # 浓墨
)

# 宣纸底色 / 卡片底色 / 边框色 / 主文字色（集中管理，CSS 与主题共用）
PAPER_BG = "#FBF8F1"       # 宣纸米白（页面背景）
CARD_BG = "#FFFDF8"        # 卡片米白
PAPER_LINE = "#E5DFD0"     # 细边框
PAPER_LINE_STRONG = "#D3CCB6"
INK_TEXT = "#26241F"       # 墨色主文字
INK_SOFT = "#6E675E"       # 淡墨次文字
VERMILION_MAIN = "#A93226" # 朱砂
VERMILION_DEEP = "#8C2A1F"
HERB_GREEN = "#5E8C5A"     # 药草绿（安全/正向提示）
AMBER = "#B98A2E"          # 琥珀（中性提醒）

# ---------------------------------------------------------------------------
# 主题构建
# ---------------------------------------------------------------------------


def build_theme():
    """构建 Gradio 主题：朱砂主色 + 墨色中性 + 宣纸底。"""
    import gradio as gr

    return gr.themes.Base(
        primary_hue=VERMILION,
        secondary_hue=INK,
        neutral_hue=INK,
        radius_size=gr.themes.sizes.radius_md,
        spacing_size=gr.themes.sizes.spacing_md,
    )


# ---------------------------------------------------------------------------
# 全局样式
# ---------------------------------------------------------------------------

GLOBAL_CSS = r"""
/* ================= 字体与基调 ================= */
:root, .gradio-container {
    --font: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'Source Han Sans SC',
            ui-sans-serif, system-ui, sans-serif;
    --font-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', 'Source Han Serif SC', serif;
}
body, .gradio-container {
    font-family: var(--font);
    color: #26241F;
}
.gradio-container {
    background: #FBF8F1 !important;
    max-width: 1180px !important;
}
.serif { font-family: var(--font-serif) !important; }

/* ================= 头部横幅（印章 + 标题） ================= */
.tcm-header {
    display: flex; align-items: center; gap: 18px;
    padding: 26px 8px 10px 8px;
    border-bottom: 1px solid #E5DFD0;
    margin-bottom: 8px;
}
.tcm-seal {
    width: 62px; height: 62px; border-radius: 50%;
    border: 3px solid #A93226; color: #A93226;
    display: flex; align-items: center; justify-content: center;
    font-family: var(--font-serif); font-size: 22px; font-weight: 700;
    letter-spacing: 2px; text-indent: 2px;
    background:
        radial-gradient(circle, transparent 52%, #FBF8F1 53%, #FBF8F1 55%, transparent 56%);
    flex: none;
}
.tcm-title { font-family: var(--font-serif); font-size: 26px; font-weight: 700;
    color: #26241F; letter-spacing: 4px; margin: 0; }
.tcm-subtitle { color: #6E675E; font-size: 13px; letter-spacing: 1.5px; margin: 4px 0 0 0; }

/* ================= 页签（书签式） ================= */
.tab-nav button {
    font-family: var(--font-serif) !important;
    letter-spacing: 2px !important;
    font-size: 15px !important;
    border-radius: 8px 8px 0 0 !important;
}
.tab-nav button.selected {
    color: #A93226 !important;
    font-weight: 700 !important;
}

/* ================= 块 / 卡片质感 ================= */
.block, .form, .panel {
    background: #FFFDF8 !important;
    border: 1px solid #E5DFD0 !important;
    box-shadow: 0 1px 3px rgba(38, 36, 31, 0.05) !important;
}
.gradio-container .tabs > div:last-child { border-top: 1px solid #E5DFD0; }

/* 输入框 */
input[type="text"], textarea, .wrap .tokenizer-input, .scroll-hide {
    background: #FFFDF8 !important;
    color: #26241F !important;
}

/* ================= 按钮 ================= */
button.primary {
    background: #A93226 !important;
    color: #FFFDF8 !important;
    font-family: var(--font-serif);
    letter-spacing: 3px;
}
button.primary:hover { background: #8C2A1F !important; }
button.secondary {
    background: #26241F !important;
    color: #FBF8F1 !important;
}

/* ================= 自定义输出卡片 ================= */
.tcm-card {
    background: #FFFDF8;
    border: 1px solid #E5DFD0;
    border-radius: 12px;
    padding: 16px 18px;
    margin: 10px 0;
    box-shadow: 0 1px 3px rgba(38, 36, 31, 0.06);
    color: #26241F;
}
.tcm-card h3 {
    font-family: var(--font-serif);
    font-size: 17px; margin: 0 0 10px 0; padding: 0 0 8px 0;
    border-bottom: 1px solid #E5DFD0; color: #26241F;
    letter-spacing: 2px; font-weight: 700;
}
.tcm-card.accent-left { border-left: 4px solid #A93226; }

/* 顶部候选大卡 */
.tcm-top1 { border-left: 4px solid #A93226; }
.tcm-top1 .name { font-family: var(--font-serif); font-size: 30px; font-weight: 700;
    color: #26241F; letter-spacing: 3px; }
.tcm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.tcm-grid-card {
    background: #FFFDF8; border: 1px solid #E5DFD0; border-radius: 10px;
    padding: 12px 14px; box-shadow: 0 1px 3px rgba(38,36,31,.05);
}
.tcm-grid-card .nm { font-family: var(--font-serif); font-weight: 700; font-size: 16px; letter-spacing: 1px; }
.tcm-grid-card .sc { color: #6E675E; font-size: 12px; margin-top: 4px; }

/* 置信度 / 匹配度进度条 */
.tcm-bar { height: 8px; background: #EFEBE0; border-radius: 99px; overflow: hidden; margin: 6px 0; }
.tcm-bar > i { display: block; height: 100%; border-radius: 99px;
    background: linear-gradient(90deg, #C13B27, #A93226); }

/* 徽章（图标+文字双重传达） */
.tcm-badge { display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 10px; border-radius: 99px; font-size: 12px; font-weight: 600; }
.tcm-badge-tox  { background: #A93226; color: #FFFDF8; }
.tcm-badge-warn { background: #B98A2E; color: #FFFDF8; }
.tcm-badge-ok   { background: #5E8C5A; color: #FFFDF8; }
.tcm-badge-gray { background: #E5DFD0; color: #4A4640; }

/* 标签行 */
.tcm-tags { margin: 8px 0; }
.tcm-tag { display: inline-block; background: #F3F0E6; color: #4A4640;
    border: 1px solid #E5DFD0; border-radius: 6px; padding: 2px 9px; margin: 2px 4px 2px 0;
    font-size: 13px; }

/* 风险 / 禁忌列表 */
.tcm-risk { background: #FDF5F3; border: 1px solid #F6D0C6; border-radius: 8px;
    padding: 8px 12px; margin: 6px 0; font-size: 13px; color: #73231A; }
.tcm-risk b { color: #A93226; }

/* 免责声明横幅 */
.tcm-disclaimer {
    margin: 14px 0 6px 0; padding: 10px 16px;
    background: #F3F0E6; border: 1px dashed #D3CCB6; border-radius: 10px;
    color: #6E675E; font-size: 13px; line-height: 1.7; letter-spacing: 0.5px;
}
.tcm-disclaimer b { color: #A93226; }

/* 页签内说明文字 */
.tcm-hint { color: #6E675E; font-size: 13px; letter-spacing: 0.5px; }
.tcm-section-title {
    font-family: var(--font-serif); font-size: 18px; font-weight: 700;
    color: #26241F; letter-spacing: 3px; margin: 18px 0 6px 0;
    padding-left: 10px; border-left: 4px solid #A93226;
}

/* 溯源来源 chip */
.tcm-source { display: inline-block; background: #F3F0E6; border: 1px solid #E5DFD0;
    border-radius: 99px; padding: 2px 12px; margin: 3px 4px 0 0; font-size: 12px; color: #4A4640; }

/* 对话气泡微调 */
.message.user { background: #FDF5F3 !important; border: 1px solid #F6D0C6 !important; }
.message.bot  { background: #FFFDF8 !important; border: 1px solid #E5DFD0 !important; }

/* 图片上传框 */
.image-upload, .upload-container { border: 1px dashed #D3CCB6 !important; border-radius: 12px !important; }
"""

# ---------------------------------------------------------------------------
# 头部横幅 & 免责声明
# ---------------------------------------------------------------------------

HEADER_HTML = """
<div class="tcm-header">
  <div class="tcm-seal">本草</div>
  <div>
    <h1 class="tcm-title">中草药多模态识别系统</h1>
    <p class="tcm-subtitle">本草图谱 · 图片识别 · 特性检索 · 药性问答 · 配伍分析</p>
  </div>
</div>
"""

DISCLAIMER_HTML = """
<div class="tcm-disclaimer">
  <b>郑重提示</b>：本系统基于 AI 识别与公开数据整理，结果仅供学习研究参考，
  <b>不构成任何医疗建议</b>。用药请务必遵医嘱，谨慎对待毒性药材与配伍禁忌。
</div>
"""
