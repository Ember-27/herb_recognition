"""新中式卡片渲染模块 —— 把结构化识别/检索结果渲染为宣纸质感 HTML 卡片。

可及性约定（UI/UX Pro Max 建议）：
- 警示信息一律「图标 + 文字」双重传达，不只用颜色；
- 对比度满足 WCAG AA（深墨文字 #26241F 于米白底 #FFFDF8）。
"""
from __future__ import annotations

import html as _html

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _esc(value) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def _badge(text: str, kind: str = "gray") -> str:
    return f'<span class="tcm-badge tcm-badge-{kind}">{_esc(text)}</span>'


def _tox_badge(toxicity) -> str:
    t = _esc(toxicity or "无毒")
    if "大毒" in t:
        return _badge(f"☠ {t}", "tox")
    if "有毒" in t or "小毒" in t:
        return _badge(f"⚠ {t}", "warn")
    return _badge(f"✔ {t}", "ok")


def _bar(ratio: float, label: str = "") -> str:
    pct = max(0.0, min(100.0, float(ratio) * 100))
    suffix = f"<span style='color:#6E675E;font-size:12px'>{_esc(label)}</span>" if label else ""
    return (f'<div class="tcm-bar"><i style="width:{pct:.1f}%"></i></div>{suffix}')


def _sample_img(image_b64: Optional[str], name: str, cls: str = "tcm-thumb") -> str:
    """渲染药材样本图（base64 data URI 内联）；无图时返回占位说明。"""
    if not image_b64:
        return (f'<div class="{cls} tcm-thumb-empty">'
                f'<span>暂无「{_esc(name)}」样本图</span></div>')
    return f'<img class="{cls}" src="{image_b64}" alt="{_esc(name)} 样本图" />'


def _tags(items) -> str:
    if not items:
        return ""
    return '<div class="tcm-tags">' + "".join(
        f'<span class="tcm-tag">{_esc(x)}</span>' for x in items
    ) + "</div>"


def _section(title: str, body: str, accent: bool = False) -> str:
    cls = "tcm-card accent-left" if accent else "tcm-card"
    return f'<div class="{cls}"><h3>{_esc(title)}</h3>{body}</div>'


def note_card(message: str, title: str = "提示") -> str:
    return _section(title, f'<p style="margin:0">{_esc(message)}</p>')


# ---------------------------------------------------------------------------
# 识别 / 检索结果主渲染
# ---------------------------------------------------------------------------


def render_predict_cards(pred: dict) -> str:
    """渲染 predict_json 的返回 dict 为一组卡片 HTML（单段）。"""
    top5 = pred.get("top5") or []
    parts: list[str] = []

    # ---- 候选区 ----
    parts.append(_candidates_block(top5, pred))

    # ---- 低置信度 / 无匹配提示 ----
    if pred.get("low_confidence") and top5:
        name = top5[0].get("name", "")
        prob = top5[0].get("prob", 0)
        parts.append(_section(
            "识别置信度提示",
            f'<div class="tcm-risk"><b>⚠ 置信度偏低</b>：本结果最高候选「{_esc(name)}」置信度仅 '
            f'{prob * 100:.1f}%，图片特征可能不明显。请尝试更清晰、单一药材的图片，'
            f'或结合文字描述再次识别。</div>'))
    if not top5 and pred.get("mode") == "text_search":
        parts.append(note_card(
            pred.get("hint") or "未匹配到药材，请补充更明确的性味/归经/功效关键词。",
            "检索提示"))

    # ---- 药性详情 ----
    info = pred.get("_info") or {}
    if info:
        parts.append(_info_card(info))

    # ---- 相似药推荐 ----
    similar = pred.get("similar") or []
    if similar:
        sim_items = "".join(
            f'<div class="tcm-grid-card">'
            f'{_sample_img(s.get("image_b64"), s.get("name", ""))}'
            f'<div class="nm">{_esc(s["name"])}</div>'
            f'<div class="sc">{_esc("、".join(s.get("categories") or []))}</div></div>'
            for s in similar)
        parts.append(_section("相似药材（功效相近）", f'<div class="tcm-grid">{sim_items}</div>'))

    # ---- 易混淆鉴别 ----
    conf = pred.get("confusable")
    if conf and conf.get("peer"):
        parts.append(_confusable_card(conf))

    # ---- 配伍风险 ----
    contra = pred.get("contraindications") or {"incompatible": [], "restraint": []}
    parts.append(_contra_card(contra))

    # ---- 经典方剂 ----
    formulas = pred.get("classic_formulas") or []
    if formulas:
        parts.append(_classic_formulas_card(formulas))

    # ---- 推荐方剂 ----
    fml = pred.get("formula") or []
    if fml:
        rows = "".join(
            f'<div class="tcm-risk"><b>方剂参考</b>：主药 {_esc(r.get("herb"))} —— '
            f'{_esc(r.get("reason"))} {_tox_badge(r.get("toxicity"))}</div>'
            for r in fml)
        parts.append(_section("推荐方剂", rows))

    return "".join(parts)


def _candidates_block(top5: list, pred: dict) -> str:
    """Top-1 大卡 + Top 2~5 网格卡。"""
    if not top5:
        return ""
    is_img = pred.get("mode") == "image"
    first = top5[0]

    if is_img:
        prob = first.get("prob", 0)
        bar = _bar(prob, f'置信度 {prob * 100:.1f}%')
    else:
        score = first.get("score", 0)
        bar = _bar(min(score / 10.0, 1.0), f'匹配分 {score}')

    sub = ""
    if not is_img:
        dims = first.get("dims") or {}
        hits = first.get("hits") or {}
        labels = []
        for key, zh in (("flavor", "性味"), ("meridian", "归经"), ("function", "功效")):
            if dims.get(key) and hits.get(key):
                labels.append(f'<span class="tcm-tag">✔ {zh}：{_esc("、".join(hits[key]))}</span>')
        if labels:
            sub = f'<div class="tcm-tags">{"".join(labels)}</div>'

    top1 = f"""
    <div class="tcm-card tcm-top1">
      <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">
        {_sample_img(first.get("image_b64"), first.get("name", ""), cls="tcm-top1-img")}
        <div style="flex:1;min-width:240px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <div class="name">{_esc(first.get("name"))}</div>
            {_tox_badge(first.get("toxicity"))}
          </div>
          {bar}
          {sub}
        </div>
      </div>
    </div>"""

    rest = top5[1:]
    if not rest:
        return top1
    cells = []
    for x in rest:
        if is_img:
            p = x.get("prob", 0)
            sc = f"{p * 100:.1f}% 置信度"
        else:
            sc = f"匹配分 {x.get('score')}"
        cells.append(
            f'<div class="tcm-grid-card">'
            f'{_sample_img(x.get("image_b64"), x.get("name", ""))}'
            f'<div class="nm">{_esc(x.get("name"))}</div>'
            f'<div class="sc">{sc}</div>{_tox_badge(x.get("toxicity"))}</div>')
    return top1 + f'<div class="tcm-grid">{"".join(cells)}</div>'


def _info_card(info: dict) -> str:
    """药性详情结构化卡片。"""
    rows = []
    property_ = info.get("property") or ""
    meridian = info.get("meridian") or ""
    function = info.get("function") or ""
    aliases = info.get("aliases") or []
    indications = info.get("indications") or ""
    cautions = info.get("cautions") or ""
    categories = info.get("categories") or []

    def kv(label, value):
        if value:
            rows.append(
                f'<p style="margin:6px 0"><span style="color:#6E675E;'
                f'font-weight:600">{label}</span>　{value}</p>')

    kv("性味", property_)
    kv("归经", meridian)
    kv("功效", function)
    if categories:
        kv("分类", _tags(categories))
    if aliases:
        kv("别名", "、".join(str(a) for a in aliases))
    kv("适应症", indications)
    kv("使用注意", cautions)
    if not rows:
        return ""
    return _section("药性详情", "".join(rows))


def _confusable_card(conf: dict) -> str:
    peer = conf.get("peer", "")
    body = f"""
    <p style="margin:0 0 8px 0;font-weight:600">「{_esc(conf.get("name") or "")}」与
    <span style="color:#A93226;font-weight:700">{_esc(peer)}</span>（{_esc(conf.get("category") or "外形相似")}）易混淆</p>
    <div class="tcm-risk"><b>易混原因</b>：{_esc(conf.get("why_confused") or "外形相似")}</div>
    <div class="tcm-risk"><b>外观差异</b>：{_esc(conf.get("appearance_diff") or "—")}</div>
    <div class="tcm-risk"><b>简易鉴别</b>：{_esc(conf.get("simple_test") or "—")}</div>
    """
    if conf.get("note"):
        body += f'<div class="tcm-risk"><b>使用提示</b>：{_esc(conf["note"])}</div>'
    return _section("相似药材鉴别", body, accent=True)


def _contra_card(contra: dict) -> str:
    inc = contra.get("incompatible") or []
    res = contra.get("restraint") or []
    if not inc and not res:
        return _section(
            "配伍风险",
            '<p style="margin:0"><span class="tcm-badge tcm-badge-ok">✔ 未检出</span>'
            ' 未发现十八反 / 十九畏配伍禁忌。</p>')
    parts = []
    if inc:
        parts.append(f"<b>十八反</b>：{'、'.join(_esc(x) for x in inc)}")
    if res:
        parts.append(f"<b>十九畏</b>：{'、'.join(_esc(x) for x in res)}")
    body = ('<div class="tcm-risk"><b>⚠ 配伍风险提示</b>：' + "；".join(parts)
            + "。含禁忌配伍的组方不可使用，务必经执业医师确认。</div>")
    return _section("配伍风险", body, accent=True)


def _classic_formulas_card(formulas: list) -> str:
    blocks = []
    for f in formulas:
        lines = [f'<div style="margin:8px 0"><b>{_esc(f.get("name"))}</b>'
                 f'<span style="color:#6E675E;font-size:12px">　{_esc(f.get("source") or "")}</span></div>']
        if f.get("category"):
            lines.append(f'<span class="tcm-tag">分类：{_esc(f["category"])}</span>')
        lines.append(f'<div style="margin:4px 0"><span style="color:#6E675E">组成</span>：'
                     f'{_esc(f.get("composition_text") or "")}</div>')
        if f.get("effects"):
            lines.append(f'<div><span style="color:#6E675E">功效</span>：{_esc(f["effects"])}</div>')
        if f.get("indications"):
            lines.append(f'<div><span style="color:#6E675E">主治</span>：{_esc(f["indications"])}</div>')
        if f.get("usage"):
            lines.append(f'<div><span style="color:#6E675E">用法</span>：{_esc(f["usage"])}</div>')
        if f.get("warning"):
            lines.append(f'<div class="tcm-risk"><b>⚠ 禁忌/注意</b>：{_esc(f["warning"])}</div>')
        blocks.append("".join(lines))
    return _section("经典方剂参考", "".join(blocks))


# ---------------------------------------------------------------------------
# 特性检索结果渲染
# ---------------------------------------------------------------------------


def render_search_cards(result: dict) -> str:
    """渲染 search_herbs_by_text 的返回 dict 为卡片网格。"""
    parsed = result.get("parsed") or {}
    total = result.get("total_conditions", 0)
    full = result.get("full") or []
    partial = result.get("partial") or []

    if total == 0:
        return note_card(
            result.get("hint")
            or "未解析出有效条件，请按「性味 + 归经 + 功效」描述，例如：味甘平，归肝肾经，滋补肝肾。",
            "检索提示")

    fn = list(dict.fromkeys(parsed.get("flavor", []) + parsed.get("nature", [])))
    cond_desc = []
    if fn:
        cond_desc.append(f'性味「{"、".join(_esc(x) for x in fn)}」')
    if parsed.get("meridian"):
        cond_desc.append(f'归经「{"、".join(_esc(x) for x in parsed["meridian"])}」')
    func_cands = list(dict.fromkeys(parsed.get("function_kws", []) + parsed.get("function_segs", [])))
    if func_cands:
        cond_desc.append(f'功效「{"、".join(_esc(x) for x in func_cands[:8])}」')

    parts = [_section(
        "解析条件",
        f'<p style="margin:0">{"，".join(cond_desc)}（共 {total} 类条件）</p>')]

    def grid(items, title):
        if not items:
            return _section(title, '<p style="margin:0;color:#6E675E">无</p>')
        cards = []
        for it in items:
            name = it.get("name", "")
            score = it.get("score", 0)
            dims = it.get("dims") or {}
            hits = it.get("hits") or {}
            info = it.get("info") or {}
            tags = []
            for key, zh in (("flavor", "性味"), ("meridian", "归经"), ("function", "功效")):
                if dims.get(key) and hits.get(key):
                    tags.append(f'<span class="tcm-tag">✔ {zh}：{_esc("、".join(hits[key]))}</span>')
            prop = info.get("property", "")
            cards.append(
                f'<div class="tcm-grid-card"><div class="nm">{_esc(name)}</div>'
                f'<div class="sc">{_esc(prop)}　匹配分 {score}</div>'
                f'<div class="tcm-tags">{"".join(tags)}</div>'
                f'{_tox_badge(info.get("toxicity"))}</div>')
        return _section(title, f'<div class="tcm-grid">{"".join(cards)}</div>')

    parts.append(grid(full, f"完全匹配（{total}/{total} 条件，共 {len(full)} 种）"))
    parts.append(grid(partial, f"部分匹配（命中 ≥1 且 <{total} 条件，共 {len(partial)} 种）"))

    if result.get("hint"):
        parts.append(note_card(result["hint"], "提示"))

    return "".join(parts)
