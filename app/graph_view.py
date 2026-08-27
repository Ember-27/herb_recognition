"""药材知识图谱可视化：生成自包含的交互式 HTML（纯 Canvas 力导向图，零外部依赖）。

不依赖 pyvis / echarts 等第三方库与 CDN，生成的 HTML 完全离线可用：
  - 节点 = 药材（圆形，颜色按功效分类；方块 = 功效分类，三角 = 归经）
  - 连线 = 关系（绿=相须相使、红=十八反、橙=十九畏、灰=分类/归经）
  - 交互：拖拽节点、滚轮缩放、空白拖动画布、点击节点高亮其配伍/禁忌并展示详情

用法:
  from app.graph_view import build_graph_html
  html = build_graph_html(kg, focus="枸杞")   # focus=None 为全图
"""
import html as html_mod
import json
from typing import Dict, Optional

from knowledge_graph.kg_builder import HerbKnowledgeGraph

# 功效分类 -> 节点颜色（与 kg_builder.FUNCTION_CATEGORY 分类键一致）
_CATEGORY_COLORS = {
    "补虚": "#e74c3c",        # 红
    "清热": "#3498db",        # 蓝
    "解表": "#27ae60",        # 绿
    "活血": "#8e44ad",        # 紫
    "利水渗湿": "#16a085",    # 青
    "安神": "#f39c12",        # 橙
    "化痰止咳": "#e67e22",    # 深橙
    "消食": "#d35400",        # 棕
    "温里": "#c0392b",        # 深红
    "其他": "#95a5a6",        # 灰
}


def build_graph_html(kg: HerbKnowledgeGraph,
                     focus: Optional[str] = None,
                     width: int = 1040, height: int = 640) -> str:
    """生成交互式知识图谱 HTML 字符串。

    参数:
      kg    : HerbKnowledgeGraph 实例
      focus : 聚焦药材名（None 表示全图浏览）
    """
    data = kg.export_graph_json(focus=focus)
    # 前端颜色表与数据一并注入
    payload = {"nodes": data["nodes"], "links": data["links"],
               "categoryColors": _CATEGORY_COLORS}
    graph_json = json.dumps(payload, ensure_ascii=False)
    inner = _HTML_TEMPLATE.replace("__GRAPH_DATA__", graph_json)
    # Gradio 6.x 的 gr.HTML 通过 innerHTML 注入，<script> 不会执行；
    # 因此把完整文档包进 <iframe srcdoc>（独立文档，JS 可正常运行）。
    safe = html_mod.escape(inner, quote=True)
    return ('<iframe srcdoc="' + safe + '" '
            f'style="width:100%;height:{height + 60}px;border:0;border-radius:10px"></iframe>')


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #f7f9fc; color: #2c3e50; }
  #wrap { position: relative; width: 100%; max-width: 1040px; margin: 0 auto; }
  canvas { display: block; width: 100%; height: auto; background: #ffffff;
           border: 1px solid #e3e8ef; border-radius: 10px; cursor: grab;
           box-shadow: 0 2px 8px rgba(31,45,61,0.06); }
  canvas.dragging { cursor: grabbing; }
  #info { position: absolute; top: 12px; right: 12px; width: 240px; max-height: 78%;
          overflow: auto; background: rgba(255,255,255,0.96); border: 1px solid #dce3ec;
          border-radius: 8px; padding: 10px 12px; font-size: 12px; line-height: 1.6;
          box-shadow: 0 2px 10px rgba(31,45,61,0.10); display: none; }
  #info h3 { font-size: 15px; color: #1f2d3d; margin-bottom: 6px;
             border-bottom: 2px solid #3498db; padding-bottom: 4px; }
  #info .tag { display: inline-block; background: #eef4fd; color: #2c6cb0;
               border-radius: 4px; padding: 1px 7px; margin: 1px 3px 1px 0; }
  #info .warn { color: #e74c3c; font-weight: bold; }
  #info .ok { color: #27ae60; font-weight: bold; }
  #info .muted { color: #7f8c8d; }
  #tip { position: absolute; pointer-events: none; background: rgba(31,45,61,0.88);
         color: #fff; font-size: 12px; padding: 3px 9px; border-radius: 5px;
         display: none; white-space: nowrap; z-index: 9; }
  #legend { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center;
            margin-top: 8px; font-size: 12px; color: #5d6d7e; }
  #legend .item { display: inline-flex; align-items: center; gap: 5px; }
  #legend .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  #legend .line { width: 22px; height: 0; border-top: 3px solid; display: inline-block; }
  #hint { font-size: 12px; color: #7f8c8d; margin-top: 6px; }
</style>
</head>
<body>
<div id="wrap">
  <canvas id="cv" width="1040" height="640"></canvas>
  <div id="info"></div>
  <div id="tip"></div>
  <div id="legend"></div>
  <div id="hint">提示：可拖拽节点 / 滚轮缩放 / 按住空白拖动平移；点击节点查看详情，再次点击空白处取消选中。</div>
</div>
<script>
"use strict";
var DATA = __GRAPH_DATA__;
var nodes = DATA.nodes.map(function (d) {
  return { id: d.id, type: d.type || "herb", focus: !!d.focus,
           property: d.property || "", meridian: d.meridian || "",
           function: d.function || "", categories: d.categories || [],
           pairs: d.pairs || [], incompatible: d.incompatible || [],
           restraint: d.restraint || [],
           x: 0, y: 0, vx: 0, vy: 0, fixed: false };
});
var links = DATA.links.map(function (l) { return { source: l.source, target: l.target, relation: l.relation }; });
var CAT_COLORS = DATA.categoryColors || {};
var meta = {};
nodes.forEach(function (n) { meta[n.id] = n; });

var W = 1040, H = 640;
var canvas = document.getElementById("cv");
var ctx = canvas.getContext("2d");
var dpr = window.devicePixelRatio || 1;
canvas.width = W * dpr; canvas.height = H * dpr;
ctx.scale(dpr, dpr);

/* ---------- 力导向参数（px 单位，按节点数自适应） ---------- */
var N = nodes.length;
var REP = N > 120 ? 26000 : 16000;
var SPRING = 0.028;
var DAMP = 0.84;
var REST_HERB = 96, REST_META = 55;

/* ---------- 确定性初始布局：按索引均匀分布在圆环上 ---------- */
(function initPos() {
  var R = Math.sqrt(N) * 17 + 40;
  var cx = W / 2, cy = H / 2;
  nodes.forEach(function (n, i) {
    var ang = (i / Math.max(N, 1)) * Math.PI * 2;
    n.x = cx + R * Math.cos(ang);
    n.y = cy + R * Math.sin(ang);
  });
})();

/* ---------- 关系样式 ---------- */
var REL_STYLE = {
  "paired":       { color: "#27ae60", width: 2.2, dash: [] },
  "incompatible": { color: "#e74c3c", width: 3.2, dash: [7, 5] },
  "restraint":    { color: "#e67e22", width: 2.2, dash: [4, 4] },
  "category":     { color: "#aab7c4", width: 1.2, dash: [] },
  "meridian":     { color: "#aab7c4", width: 1.2, dash: [] }
};
var REL_LABEL = { "paired": "相须相使", "incompatible": "十八反",
                  "restraint": "十九畏", "category": "功效分类", "meridian": "归经" };

function herbColor(n) {
  var c = (n.categories && n.categories[0]) || "其他";
  return CAT_COLORS[c] || CAT_COLORS["其他"];
}

/* ---------- 视图变换：zoom / pan ---------- */
var zoom = 1, panX = 0, panY = 0;
function toWorld(e) {
  var rect = canvas.getBoundingClientRect();
  var mx = (e.clientX - rect.left) * (W / rect.width);
  var my = (e.clientY - rect.top) * (H / rect.height);
  return { x: (mx - panX) / zoom, y: (my - panY) / zoom };
}

/* ---------- 力导向模拟 ---------- */
function simulate() {
  var i, j, a, b, dx, dy, d, f, fx, fy;
  for (i = 0; i < N; i++) {
    a = nodes[i];
    for (j = i + 1; j < N; j++) {
      b = nodes[j];
      dx = a.x - b.x; dy = a.y - b.y;
      d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1) { d = 1; dx = 1; dy = 0; }
      f = REP / (d * d);
      fx = dx / d * f; fy = dy / d * f;
      if (!a.fixed) { a.vx += fx; a.vy += fy; }
      if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
    }
  }
  for (i = 0; i < links.length; i++) {
    var l = links[i];
    a = meta[l.source]; b = meta[l.target];
    if (!a || !b) continue;
    dx = b.x - a.x; dy = b.y - a.y;
    d = Math.sqrt(dx * dx + dy * dy) || 1;
    var rest = (a.type !== "herb" || b.type !== "herb") ? REST_META : REST_HERB;
    f = SPRING * (d - rest);
    fx = dx / d * f; fy = dy / d * f;
    if (!a.fixed) { a.vx += fx; a.vy += fy; }
    if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
  }
  for (i = 0; i < N; i++) {
    a = nodes[i];
    if (a.fixed) continue;
    a.vx += (W / 2 - a.x) * 0.002;
    a.vy += (H / 2 - a.y) * 0.002;
    a.vx *= DAMP; a.vy *= DAMP;
    var sp = Math.sqrt(a.vx * a.vx + a.vy * a.vy);
    if (sp > 10) { a.vx = a.vx / sp * 10; a.vy = a.vy / sp * 10; }
    a.x += a.vx; a.y += a.vy;
    if (a.x < -200) a.x = -200; if (a.x > W + 200) a.x = W + 200;
    if (a.y < -200) a.y = -200; if (a.y > H + 200) a.y = H + 200;
  }
}

/* ---------- 选中高亮 ---------- */
var selected = null;
var hlSet = new Set();
function updateHighlight() {
  hlSet.clear();
  if (selected) {
    hlSet.add(selected.id);
    links.forEach(function (l) {
      if (l.source === selected.id) hlSet.add(l.target);
      if (l.target === selected.id) hlSet.add(l.source);
    });
  }
}

/* ---------- 详情面板 ---------- */
function showDetail(n) {
  var info = document.getElementById("info");
  var parts = [];
  var relOf = function (id) {
    var r = [];
    links.forEach(function (l) {
      var other = null, rel = null;
      if (l.source === n.id && l.target === id) { other = id; rel = l.relation; }
      else if (l.target === n.id && l.source === id) { other = id; rel = l.relation; }
      if (other && rel !== "category" && rel !== "meridian") r.push(rel);
    });
    return r;
  };
  if (n.type === "herb") {
    parts.push("<h3>" + n.id + "</h3>");
    parts.push("<div>药性：" + (n.property || "—") + "</div>");
    parts.push("<div>归经：" + (n.meridian || "—") + "</div>");
    parts.push("<div>功效：" + (n["function"] || "—") + "</div>");
    parts.push("<div>功效分类：" + (n.categories || []).map(function (c) {
      return '<span class="tag" style="background:' + (CAT_COLORS[c] || "#eee") + '22;color:' + (CAT_COLORS[c] || "#666") + '">' + c + "</span>";
    }).join("") + "</div>");
    if (n.pairs && n.pairs.length) {
      parts.push("<div>常用配伍：<span class='ok'>" + n.pairs.join("、") + "</span></div>");
    } else {
      parts.push("<div>常用配伍：<span class='muted'>无</span></div>");
    }
    parts.push("<div>十八反：" + (n.incompatible && n.incompatible.length
      ? "<span class='warn'>" + n.incompatible.join("、") + "</span>"
      : "<span class='muted'>无</span>") + "</div>");
    parts.push("<div>十九畏：" + (n.restraint && n.restraint.length
      ? "<span class='warn'>" + n.restraint.join("、") + "</span>"
      : "<span class='muted'>无</span>") + "</div>");
    if (n.focus) parts.push("<div class='muted'>当前聚焦药材</div>");
  } else if (n.type === "category") {
    parts.push("<h3>功效分类：<span class='tag'>" + n.id + "</span></h3>");
    parts.push("<div>所属药材见图中与其相连的节点。</div>");
  } else if (n.type === "meridian") {
    parts.push("<h3>归经：" + n.id + "</h3>");
    parts.push("<div>归属该经的药材见图中相连节点。</div>");
  }
  var rels = {};
  n.pairs && n.pairs.forEach(function (p) { rels[p] = "paired"; });
  n.incompatible && n.incompatible.forEach(function (p) { rels[p] = "incompatible"; });
  n.restraint && n.restraint.forEach(function (p) { rels[p] = "restraint"; });
  if (n.type === "herb" && Object.keys(rels).length) {
    parts.push("<div style='margin-top:5px' class='muted'>图中关系：</div>");
    Object.keys(rels).forEach(function (id) {
      var st = REL_STYLE[rels[id]];
      parts.push("<div><span style='display:inline-block;width:16px;border-top:2px solid " + st.color + "'></span> " + id + "（" + (REL_LABEL[rels[id]] || rels[id]) + "）</div>");
    });
  }
  info.innerHTML = parts.join("");
  info.style.display = "block";
}

/* ---------- 绘制 ---------- */
function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(panX, panY);
  ctx.scale(zoom, zoom);

  links.forEach(function (l) {
    var a = meta[l.source], b = meta[l.target];
    if (!a || !b) return;
    var st = REL_STYLE[l.relation] || REL_STYLE.category;
    var active = !selected || (hlSet.has(a.id) && hlSet.has(b.id));
    ctx.globalAlpha = active ? 0.85 : 0.08;
    ctx.strokeStyle = st.color;
    ctx.lineWidth = st.width;
    ctx.setLineDash(st.dash);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  nodes.forEach(function (n) {
    var active = !selected || hlSet.has(n.id);
    ctx.globalAlpha = active ? 1 : 0.13;
    if (n.type === "herb") {
      var r = n.focus ? 16 : 11;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = herbColor(n);
      ctx.fill();
      ctx.lineWidth = n.focus ? 3 : 1.5;
      ctx.strokeStyle = n.focus ? "#1f2d3d" : "rgba(0,0,0,0.35)";
      ctx.stroke();
      if (selected && selected.id === n.id) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = "#2c6cb0";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.fillStyle = "#fff";
      ctx.font = (n.focus ? "bold 12px" : "11px") + " 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      var label = n.id.length > 4 ? n.id.slice(0, 4) + "…" : n.id;
      ctx.fillText(label, n.x, n.y + 1);
    } else {
      var s = 9;
      ctx.fillStyle = n.type === "category" ? "#95a5a6" : "#e67e22";
      ctx.beginPath();
      if (n.type === "category") {
        ctx.rect(n.x - s, n.y - s, s * 2, s * 2);
      } else {
        ctx.moveTo(n.x, n.y - s);
        ctx.lineTo(n.x + s, n.y + s * 0.75);
        ctx.lineTo(n.x - s, n.y + s * 0.75);
        ctx.closePath();
      }
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,0.3)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#555";
      ctx.font = "10px 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(n.id, n.x, n.y + s + 10);
    }
  });
  ctx.restore();
  ctx.globalAlpha = 1;
}

/* ---------- 图例 ---------- */
(function buildLegend() {
  var legend = document.getElementById("legend");
  var items = [];
  Object.keys(CAT_COLORS).forEach(function (c) {
    items.push('<span class="item"><span class="dot" style="background:' + CAT_COLORS[c] + '"></span>' + c + "</span>");
  });
  ["paired", "incompatible", "restraint"].forEach(function (r) {
    var st = REL_STYLE[r];
    items.push('<span class="item"><span class="line" style="border-color:' + st.color + (st.dash.length ? ";border-top-style:dashed" : "") + '"></span>' + (REL_LABEL[r]) + "</span>");
  });
  items.push('<span class="item"><span class="dot" style="background:#95a5a6;border-radius:2px"></span>功效分类</span>');
  items.push('<span class="item"><span class="dot" style="background:#e67e22;border-radius:2px;clip-path:polygon(50% 0,100% 100%,0 100%)"></span>归经</span>');
  legend.innerHTML = items.join("");
})();

/* ---------- 交互：拖拽 / 平移 / 缩放 / 点击 / hover ---------- */
var draggingNode = null, panning = false;
var downPos = { x: 0, y: 0 }, moved = false;

function hitNode(wx, wy) {
  for (var i = nodes.length - 1; i >= 0; i--) {
    var n = nodes[i];
    var r = (n.type === "herb" ? (n.focus ? 16 : 11) : 12) + 3;
    var dx = wx - n.x, dy = wy - n.y;
    if (dx * dx + dy * dy <= r * r) return n;
  }
  return null;
}

canvas.addEventListener("mousedown", function (e) {
  var w = toWorld(e);
  var n = hitNode(w.x, w.y);
  downPos = { x: e.clientX, y: e.clientY };
  moved = false;
  if (n) {
    draggingNode = n;
    n.fixed = true;
    canvas.classList.add("dragging");
  } else {
    panning = true;
    panStart = { x: e.clientX, y: e.clientY, px: panX, py: panY };
  }
});
var panStart = { x: 0, y: 0, px: 0, py: 0 };

canvas.addEventListener("mousemove", function (e) {
  var w = toWorld(e);
  if (draggingNode) {
    if (Math.abs(e.clientX - downPos.x) + Math.abs(e.clientY - downPos.y) > 3) moved = true;
    draggingNode.x = w.x;
    draggingNode.y = w.y;
    return;
  }
  if (panning) {
    panX = panStart.px + (e.clientX - panStart.x);
    panY = panStart.py + (e.clientY - panStart.y);
    return;
  }
  var n = hitNode(w.x, w.y);
  var tip = document.getElementById("tip");
  if (n) {
    var rect = canvas.getBoundingClientRect();
    tip.style.display = "block";
    tip.style.left = (e.clientX - rect.left + 12) + "px";
    tip.style.top = (e.clientY - rect.top - 8) + "px";
    tip.textContent = n.type === "herb"
      ? n.id + "（" + ((n.categories && n.categories[0]) || "其他") + "）"
      : (n.type === "category" ? "功效分类：" : "归经：") + n.id;
  } else {
    tip.style.display = "none";
  }
});

canvas.addEventListener("mouseup", function (e) {
  if (draggingNode) {
    draggingNode.fixed = false;
    draggingNode = null;
    canvas.classList.remove("dragging");
    if (!moved) {
      var w = toWorld(e);
      var n = hitNode(w.x, w.y);
      selected = (selected && selected.id === (n && n.id)) ? null : n;
      updateHighlight();
      if (selected) showDetail(selected);
      else document.getElementById("info").style.display = "none";
    }
  }
  panning = false;
});

canvas.addEventListener("mouseleave", function () {
  document.getElementById("tip").style.display = "none";
});

canvas.addEventListener("wheel", function (e) {
  e.preventDefault();
  var w = toWorld(e);
  var k = e.deltaY > 0 ? 1 / 1.15 : 1.15;
  zoom = Math.max(0.25, Math.min(4, zoom * k));
  panX = w.x - (w.x - panX) * (k);  // 保持鼠标下的点不动
  panY = w.y - (w.y - panY) * (k);
});

/* ---------- 主循环 ---------- */
var frame = 0;
(function loop() {
  simulate();
  draw();
  frame++;
  requestAnimationFrame(loop);
})();
</script>
</body>
</html>
"""
