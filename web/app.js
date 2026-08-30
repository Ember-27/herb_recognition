/* ============================================================
   本草识鉴 · 前端逻辑（纯 HTML/CSS/JS 单页应用）
   对接 FastAPI: /health /predict /search /explain /chat /graph /herbs
   ============================================================ */
"use strict";

/* ---------- 工具 ---------- */
function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 统一中药名展示格式：去掉括号拼音后缀（人参(renshen) -> 人参），数据文件保留原样 */
function cleanName(name) {
  if (!name || typeof name !== "string") return name || "";
  var idx = name.indexOf("(");
  return idx > 0 ? name.slice(0, idx).trim() : name;
}

/** 简易 Markdown：加粗 / 换行 / 无序列表 / 空行分段 */
function mdToHtml(md) {
  if (!md) return "";
  let lines = String(md).split("\n");
  let html = "", listOpen = false;
  for (let line of lines) {
    line = esc(line);
    let listMatch = line.match(/^\s*[-*•]\s+(.*)/);
    if (listMatch) {
      if (!listOpen) { html += '<ul class="md-list">'; listOpen = true; }
      html += "<li>" + listMatch[1].replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</li>";
      continue;
    }
    if (listOpen) { html += "</ul>"; listOpen = false; }
    line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    if (line.trim() === "") { html += "<br>"; continue; }
    html += line + "<br>";
  }
  if (listOpen) html += "</ul>";
  return html;
}

function nowTime() {
  const d = new Date();
  return String(d.getHours()).padStart(2, "0") + ":" +
         String(d.getMinutes()).padStart(2, "0");
}

async function postForm(url, formData) {
  const resp = await fetch(url, { method: "POST", body: formData });
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

/* ---------- 页签切换 ---------- */
$$(".tab").forEach(function (tab) {
  tab.addEventListener("click", function () {
    $$(".tab").forEach(t => { t.classList.remove("active"); t.setAttribute("aria-selected", "false"); });
    $$(".module").forEach(m => m.classList.remove("active"));
    tab.classList.add("active");
    tab.setAttribute("aria-selected", "true");
    $("#" + tab.dataset.tab).classList.add("active");
    // 图谱模块首次激活时自动加载
    if (tab.dataset.tab === "graph" && !window._graphLoaded) {
      loadGraph("");
      window._graphLoaded = true;
    }
  });
});

/* ============================================================
   通用上传组件（拖拽 + 点击 + Ctrl+V 粘贴）
   root: 上传器容器，内含 [data-file-input] [data-drop-zone] [data-clear]
   ============================================================ */
var uploadState = {
  recognize: { file: null, preview: null },
  gradcam: { file: null, preview: null },
};

function initUploader(root, key) {
  var input = $("[data-file-input]", root);
  var drop = $("[data-drop-zone]", root);
  var preview = $(".uploader-preview", root);
  var img = $("img", preview);
  var clear = $("[data-clear]", root);

  function setFile(file) {
    if (!file || !file.type.startsWith("image/")) return;
    uploadState[key].file = file;
    var url = URL.createObjectURL(file);
    uploadState[key].preview = url;
    img.src = url;
    preview.hidden = false;
    drop.style.display = "none";
  }

  drop.addEventListener("click", () => input.click());
  $$("[data-click-hint]", root).forEach(function (el) {
    el.addEventListener("click", e => { e.stopPropagation(); input.click(); });
  });
  input.addEventListener("change", () => setFile(input.files[0]));
  clear.addEventListener("click", () => {
    uploadState[key].file = null;
    if (uploadState[key].preview) URL.revokeObjectURL(uploadState[key].preview);
    uploadState[key].preview = null;
    input.value = "";
    preview.hidden = true;
    drop.style.display = "";
  });

  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("drag-over");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("drag-over");
  }));
  drop.addEventListener("drop", e => {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) setFile(f);
  });
}

initUploader($(".uploader[data-uploader='recognize']"), "recognize");
initUploader($(".uploader[data-uploader='gradcam']"), "gradcam");

/* Ctrl+V 粘贴图片：优先投递给当前可见模块的上传器，否则投递给聊天附件 */
document.addEventListener("paste", function (e) {
  var items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (var it of items) {
    if (it.type && it.type.startsWith("image/")) {
      var file = it.getAsFile();
      var active = $(".module.active");
      var activeKey = active && active.id === "recognize" ? "recognize"
                    : active && active.id === "gradcam" ? "gradcam" : null;
      if (activeKey) {
        var root = $(".uploader[data-uploader='" + activeKey + "']");
        var input = $("[data-file-input]", root);
        var drop = $("[data-drop-zone]", root);
        var preview = $(".uploader-preview", root);
        var imgEl = $("img", preview);
        uploadState[activeKey].file = file;
        uploadState[activeKey].preview = URL.createObjectURL(file);
        imgEl.src = uploadState[activeKey].preview;
        preview.hidden = false;
        drop.style.display = "none";
      } else {
        chatAttachFile(file);
      }
      e.preventDefault();
      break;
    }
  }
});

/* ============================================================
   模块 1：图片识别 → 药材档案大卡
   ============================================================ */
$("[data-identify]").addEventListener("click", async function () {
  var btn = this;
  var imgFile = uploadState.recognize.file;
  var text = $("#recognize-text").value.trim();
  var resultBox = $("#recognize-result");
  var loading = $(".result-loading", resultBox);
  var cards = $("#recognize-cards");

  if (!imgFile && !text) {
    alert("请上传图片或输入文字描述。");
    return;
  }
  resultBox.hidden = false;
  loading.hidden = false;
  cards.innerHTML = "";
  btn.disabled = true;
  try {
    var fd = new FormData();
    if (imgFile) fd.append("image", imgFile);
    fd.append("text", text);
    var data = await postForm("/predict", fd);
    loading.hidden = true;
    if (data.error) { cards.innerHTML = '<div class="tcm-risk">' + esc(data.message) + "</div>"; return; }
    renderPredict(data);
  } catch (err) {
    loading.hidden = true;
    cards.innerHTML = '<div class="tcm-risk">请求失败：' + esc(err.message) + "</div>";
  } finally {
    btn.disabled = false;
  }
});

/* ============================================================
   收藏夹（后端 JSON 持久化）
   ============================================================ */
var favTab = "herb";          // 当前抽屉内显示的收藏分类
var favData = { herbs: [], chats: [] };

function favStarHerb(name, extra) {
  // 生成药材收藏星标按钮；extra 为附带的 info 对象（可选）
  var info = extra ? encodeURIComponent(JSON.stringify(extra)) : "";
  return '<button type="button" class="fav-star" data-fav-herb="' + esc(name) +
    '" data-fav-info="' + info + '" title="收藏该药材">★</button>';
}

async function apiAddHerb(name, info) {
  try {
    var fd = new FormData();
    fd.append("name", name);
    if (info) fd.append("info", JSON.stringify(info));
    var resp = await fetch("/favorites/herb", { method: "POST", body: fd });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();
    if (data.ok && data.duplicate) {
      toast("「" + name + "」已在收藏中");
    } else if (data.ok) {
      toast("已收藏「" + name + "」");
    }
    await refreshFavCount();
    return data;
  } catch (e) {
    toast("收藏失败：" + e.message);
  }
}

async function apiAddChat(question, answer, ragSources, imageBase64) {
  try {
    var fd = new FormData();
    fd.append("question", question || "");
    fd.append("answer", answer || "");
    if (ragSources && ragSources.length) fd.append("rag_sources", JSON.stringify(ragSources));
    if (imageBase64) fd.append("image", imageBase64);
    var resp = await fetch("/favorites/chat", { method: "POST", body: fd });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();
    if (data.ok) toast("已收藏对话");
    await refreshFavCount();
    return data;
  } catch (e) {
    toast("收藏失败：" + e.message);
  }
}

async function apiRemoveFav(fid) {
  try {
    var resp = await fetch("/favorites?fid=" + encodeURIComponent(fid), { method: "DELETE" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    await refreshFavCount();
    if (!$("#favDrawer").hidden) renderFavList();
  } catch (e) {
    toast("删除失败：" + e.message);
  }
}

async function apiClearFav(kind) {
  try {
    var resp = await fetch("/favorites/clear?type=" + (kind || ""), { method: "DELETE" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    await refreshFavCount();
    if (!$("#favDrawer").hidden) renderFavList();
  } catch (e) {
    toast("清空失败：" + e.message);
  }
}

async function refreshFavCount() {
  try {
    var resp = await fetch("/favorites");
    if (!resp.ok) return;
    favData = await resp.json();
    var total = (favData.herbs || []).length + (favData.chats || []).length;
    var badge = $("#favCount");
    if (total > 0) { badge.hidden = false; badge.textContent = total; }
    else { badge.hidden = true; }
  } catch (e) { /* 忽略 */ }
}

function openFavDrawer() {
  $("#favDrawer").hidden = false;
  $("#favMask").hidden = false;
  renderFavList();
}
function closeFavDrawer() {
  $("#favDrawer").hidden = true;
  $("#favMask").hidden = true;
}

function renderFavList() {
  var list = $("#favList");
  var empty = $("#favEmpty");
  list.innerHTML = "";
  var items = favTab === "herb" ? (favData.herbs || []) : (favData.chats || []);
  if (!items.length) {
    empty.hidden = false;
    empty.textContent = favTab === "herb"
      ? "暂无收藏药材，点击药材卡片上的 ★ 即可收藏。"
      : "暂无收藏对话，点击对话回答下的 ★ 即可收藏。";
    return;
  }
  empty.hidden = true;
  items.forEach(function (it) {
    var card = document.createElement("div");
    card.className = "fav-card";
    if (favTab === "herb") {
      card.innerHTML =
        '<div class="fav-card-main">' +
          '<div class="fav-card-title">' + esc(cleanName(it.name)) + "</div>" +
          (it.info && it.info.property ? '<div class="fav-card-sub">' + esc(it.info.property) + "</div>" : "") +
        "</div>";
      card.addEventListener("click", function (e) {
        if (e.target.closest(".fav-del")) return;
        // 点击药材跳转关系图谱聚焦
        var gname = cleanName(it.name);
        $("#graph-focus").value = gname;
        $$(".tab").forEach(function (t) {
          if (t.dataset.tab === "graph") t.click();
        });
        loadGraph(gname);
        closeFavDrawer();
      });
    } else {
      var qDec = safeDecode(it.question || "（图片提问）");
      var ansSrc = safeDecode(it.answer || "").replace(/[#*`>_~]/g, " ").replace(/\s+/g, " ").trim();
      if (ansSrc.length > 80) ansSrc = ansSrc.slice(0, 80) + "…";
      card.innerHTML =
        '<div class="fav-card-main">' +
          '<div class="fav-card-title">问：' + esc(qDec) + "</div>" +
          '<div class="fav-card-sub">' + esc(ansSrc) + "</div>" +
          '<div class="fav-card-hint">点击查看完整对话详情</div>' +
        "</div>";
      card.classList.add("fav-card-click");
      card.addEventListener("click", function (e) {
        if (e.target.closest(".fav-del")) return;
        openFavDetail(it);
      });
    }
    var del = document.createElement("button");
    del.type = "button";
    del.className = "fav-del";
    del.textContent = "✕";
    del.title = "移除收藏";
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      apiRemoveFav(it.fid, favTab);
      card.remove();
      var left = $$(".fav-card", list);
      if (!left.length) { empty.hidden = false; }
    });
    card.appendChild(del);
    list.appendChild(card);
  });
}

/* 轻量提示 */
var _toastTimer = null;
function toast(msg) {
  var t = $("#toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function () { t.classList.remove("show"); }, 1800);
}

/* 收藏抽屉事件绑定 */
(function initFavDrawer() {
  $("#favToggle").addEventListener("click", openFavDrawer);
  $("#favClose").addEventListener("click", closeFavDrawer);
  $("#favMask").addEventListener("click", closeFavDrawer);
  $$(".fav-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      $$(".fav-tab").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      favTab = tab.dataset.favtab;
      renderFavList();
    });
  });
  $("#favClear").addEventListener("click", function () {
    if (!confirm("确定清空当前分类（" + (favTab === "herb" ? "药材" : "对话") + "）的收藏？")) return;
    apiClearFav(favTab);
  });
  // 全局委托：药材卡片上的收藏按钮
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-fav-herb]");
    if (btn) {
      e.preventDefault();
      var name = cleanName(btn.getAttribute("data-fav-herb"));
      var infoRaw = btn.getAttribute("data-fav-info");
      var info = null;
      if (infoRaw) { try { info = JSON.parse(decodeURIComponent(infoRaw)); } catch (_) {} }
      apiAddHerb(name, info);
    }
    var cbtn = e.target.closest("[data-fav-chat]");
    if (cbtn) {
      e.preventDefault();
      var q = cbtn.getAttribute("data-fav-chat");
      var a = cbtn.getAttribute("data-fav-answer");
      var s = cbtn.getAttribute("data-fav-sources");
      try { q = decodeURIComponent(q); } catch (_) {}
      try { a = decodeURIComponent(a); } catch (_) {}
      var sources = [];
      if (s) { try { sources = JSON.parse(decodeURIComponent(s)); } catch (_) {} }
      apiAddChat(q, a, sources, lastChatImgBase64);
    }
  });
})();

/* 收藏对话详情弹窗 */
function safeDecode(str) {
  if (!str || typeof str !== "string") return str;
  if (!/%[0-9A-Fa-f]{2}/.test(str)) return str;
  try { return decodeURIComponent(str); } catch (_) { return str; }
}
function openFavDetail(item) {
  var modal = $("#favDetail");
  var body = $("#favDetailBody");
  var html = "";
  html += '<div class="fav-detail-q"><span class="fav-detail-label">问</span>' + esc(safeDecode(item.question || "（图片提问）")) + "</div>";
  if (item.image) {
    html += '<div class="fav-detail-img"><img src="' + esc(item.image) + '" alt="附图"></div>';
  }
  html += '<div class="fav-detail-a"><span class="fav-detail-label">答</span>' + mdToHtml(safeDecode(item.answer || "（无内容）")) + "</div>";
  var sources = item.rag_sources || [];
  if (sources.length) {
    html += '<details class="rag-source" open><summary>知识库来源（' + sources.length + "）</summary><div class='rag-body'>";
    sources.forEach(function (s) {
      html += '<div class="rag-item">' + esc(s.title || s.name || "") + (s.meta ? " · " + esc(s.meta) : "") + "</div>";
    });
    html += "</div></details>";
  }
  body.innerHTML = html;
  modal.hidden = false;
  $("#favDetailMask").hidden = false;
}
function closeFavDetail() {
  $("#favDetail").hidden = true;
  $("#favDetailMask").hidden = true;
}
$("#favDetailMask").addEventListener("click", closeFavDetail);
$$("[data-fav-detail-close]").forEach(function (b) { b.addEventListener("click", closeFavDetail); });

// 进入页面即同步一次收藏数量
refreshFavCount();

function toxBadge(tox) {
  if (!tox) return "";
  if (tox === "大毒" || tox === "有毒") return '<span class="tcm-badge tcm-badge-tox">⚠ ' + esc(tox) + "</span>";
  if (tox === "小毒" || tox === "微毒") return '<span class="tcm-badge tcm-badge-warn">⚠ ' + esc(tox) + "</span>";
  return '<span class="tcm-badge tcm-badge-ok">' + esc(tox) + "</span>";
}

function renderPredict(data) {
  var cards = $("#recognize-cards");
  var html = "";
  var top5 = data.top5 || [];
  var isImg = data.mode === "image";
  var top1 = top5[0];
  var rest = top5.slice(1);

  if (isImg && top1) {
    html += '<div class="tcm-section-title">识别结果（Top-1）</div>';
    html += '<div class="tcm-card" style="border-left:4px solid var(--vermilion)">';
    html += "<h4>" + esc(cleanName(top1.name)) + favStarHerb(cleanName(top1.name), top1) + "</h4>";
    html += toxBadge(top1.toxicity);
    if (data.low_confidence) {
      html += '<span class="tcm-badge tcm-badge-warn">置信度较低，建议人工复核</span>';
    }
    html += '<div class="tcm-bar"><span style="width:' + (top1.prob * 100).toFixed(1) + '%"></span></div>';
    html += '<div class="detail-muted">置信度 ' + (top1.prob * 100).toFixed(1) + "%</div>";
    html += "</div>";
  }

  if (rest.length) {
    html += '<div class="tcm-section-title">候选（Top-2 ~ 5）</div>';
    html += '<div class="tcm-grid">';
    rest.forEach(function (it) {
      html += '<div class="tcm-card">';
      html += "<h4>" + esc(cleanName(it.name)) + favStarHerb(cleanName(it.name), it) + "</h4>";
      html += toxBadge(it.toxicity);
      html += '<div class="tcm-bar"><span style="width:' + (it.prob * 100).toFixed(1) + '%"></span></div>';
      html += '<div class="detail-muted">' + (it.prob * 100).toFixed(1) + "%</div>";
      html += "</div>";
    });
    html += "</div>";
  }

  // 特性检索模式的 Top5（text_search 模式：无 prob，有 score/dims/hits/info）
  if (!isImg) {
    html += '<div class="tcm-section-title">检索结果 Top-5</div>';
    html += '<div class="tcm-grid">';
    top5.forEach(function (it) {
      var info = it.info || {};
      var hitChips = "";
      if (it.hits) {
        Object.keys(it.hits).forEach(function (k) {
          (it.hits[k] || []).forEach(function (w) {
            hitChips += '<span class="hit-chip">' + esc(k) + ":" + esc(w) + "</span>";
          });
        });
      }
      html += '<div class="tcm-card">';
      html += "<h4>" + esc(cleanName(it.name)) + favStarHerb(cleanName(it.name), it) + "</h4>";
      html += toxBadge(it.toxicity);
      if (hitChips) html += '<div style="margin-top:4px">' + hitChips + "</div>";
      if (info.property) html += '<div class="detail-line"><span class="k">药性：</span>' + esc(info.property) + "</div>";
      if (info.meridian) html += '<div class="detail-line"><span class="k">归经：</span>' + esc(info.meridian) + "</div>";
      if (info.function) html += '<div class="detail-line"><span class="k">功效：</span>' + esc(info.function) + "</div>";
      html += '<div class="detail-muted">匹配度 ' + esc(it.score) + " 分</div>";
      html += "</div>";
    });
    html += "</div>";
  }

  // 药性详情
  if (data.kg_info) {
    html += '<div class="tcm-section-title">药性详情（知识图谱）</div>';
    html += '<div class="tcm-card">' + mdToHtml(data.kg_info) + "</div>";
  }

  // 相似药
  if (data.similar && data.similar.length) {
    html += '<div class="tcm-section-title">相似药推荐</div>';
    html += '<div class="tcm-grid">';
    data.similar.forEach(function (s) {
      html += '<div class="tcm-card">';
      html += "<h4>" + esc(cleanName(s.name)) + favStarHerb(cleanName(s.name), s) + "</h4>";
      if (s.categories && s.categories.length) {
        html += s.categories.map(c => '<span class="tcm-badge tcm-badge-gray">' + esc(c) + "</span>").join("");
      }
      html += "</div>";
    });
    html += "</div>";
  }

  // 易混淆药材
  if (data.confusable && data.confusable.peer) {
    html += '<div class="tcm-section-title">易混淆药材鉴别</div>';
    html += '<div class="tcm-risk">⚠ 与 <strong>' + esc(cleanName(data.confusable.peer)) + "</strong> 外观相似，请注意鉴别。</div>";
    if (data.confusable.points && data.confusable.points.length) {
      html += '<div class="tcm-card"><ul class="md-list">';
      data.confusable.points.forEach(function (p) {
        html += "<li>" + mdToHtml(p) + "</li>";
      });
      html += "</ul></div>";
    }
  }

  // 配伍风险（安全红线）
  var contra = data.contraindications || {};
  if ((contra.incompatible && contra.incompatible.length) ||
      (contra.restraint && contra.restraint.length)) {
    html += '<div class="tcm-section-title">配伍风险提示</div>';
    html += '<div class="tcm-risk">';
    if (contra.incompatible && contra.incompatible.length) {
      html += "十八反禁忌：<strong>" + esc(contra.incompatible.join("、")) + "</strong><br>";
    }
    if (contra.restraint && contra.restraint.length) {
      html += "十九畏禁忌：<strong>" + esc(contra.restraint.join("、")) + "</strong><br>";
    }
    html += "（方剂推荐已自动规避，含禁忌配伍的组方不可使用）";
    html += "</div>";
  }

  // 经典方剂
  if (data.classic_formulas && data.classic_formulas.length) {
    html += '<div class="tcm-section-title">经典方剂参考</div>';
    data.classic_formulas.forEach(function (f) {
      html += '<div class="tcm-card">';
      html += "<h4>📜 " + esc(f.name) + "</h4>";
      if (f.source) html += '<div class="detail-line"><span class="k">出处：</span>' + esc(f.source) + "</div>";
      if (f.effects) html += '<div class="detail-line"><span class="k">功效：</span>' + esc(f.effects) + "</div>";
      if (f.warning) html += '<div class="detail-line" style="color:var(--vermilion-deep)">⚠ ' + esc(f.warning) + "</div>";
      html += "</div>";
    });
  }

  // 推荐方剂
  if (data.formula && data.formula.length) {
    html += '<div class="tcm-section-title">推荐方剂（辨证参考）</div>';
    data.formula.forEach(function (r) {
      html += '<div class="tcm-card">';
      html += "<h4>" + esc(r.herb) + "</h4>";
      if (r.reason) html += '<div class="detail-line"><span class="k">依据：</span>' + esc(r.reason) + "</div>";
      html += "</div>";
    });
  }

  // 免责声明
  html += '<div class="tcm-disclaimer" style="margin-top:16px">⚠ <strong>医疗风险提示</strong>：以上内容仅供科普与学习参考，不构成医疗诊断或用药建议，请咨询执业中医师或药师。</div>';
  cards.innerHTML = html;
}

/* ============================================================
   模块 2：特性检索 → 卡片网格
   ============================================================ */
$("[data-search]").addEventListener("click", async function () {
  var btn = this;
  var text = $("#search-text").value.trim();
  var resultBox = $("#search-result");
  var loading = $(".result-loading", resultBox);
  if (!text) { alert("请输入检索条件。"); return; }
  resultBox.hidden = false;
  loading.hidden = false;
  btn.disabled = true;
  try {
    var resp = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();
    loading.hidden = true;
    renderSearch(data);
  } catch (err) {
    loading.hidden = true;
    alert("检索失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderSearch(data) {
  var resultBox = $("#search-result");
  var summaryEl = $("#search-summary");
  var fullEl = $("#search-full");
  var partialEl = $("#search-partial");
  resultBox.hidden = false;
  var res = data.result || {};

  if (res.hint && !(res.full && res.full.length)) {
    summaryEl.hidden = false;
    summaryEl.innerHTML = esc(res.hint);
    fullEl.hidden = true;
    partialEl.hidden = true;
    return;
  }

  // 解析条件摘要
  var parsed = res.parsed || {};
  var condChips = [];
  (parsed.flavor || []).concat(parsed.nature || []).forEach(w => condChips.push(w));
  (parsed.meridian || []).forEach(w => condChips.push(w));
  (parsed.function_kws || []).forEach(w => condChips.push(w));
  summaryEl.hidden = false;
  summaryEl.innerHTML = "已解析条件（共 " + (res.total_conditions || 0) + " 类）：" +
    (condChips.map(w => '<span class="hit-chip">' + esc(w) + "</span>").join("") || "未解析出有效条件");

  // 完全匹配
  if (res.full && res.full.length) {
    fullEl.hidden = false;
    fullEl.innerHTML = "<h3>完全匹配（" + res.full.length + "）</h3>" + searchGrid(res.full, "full");
  } else {
    fullEl.hidden = true;
  }
  // 部分匹配
  if (res.partial && res.partial.length) {
    partialEl.hidden = false;
    partialEl.innerHTML = "<h3>部分匹配（" + res.partial.length + "）</h3>" + searchGrid(res.partial, "partial");
  } else {
    partialEl.hidden = true;
  }
}

function searchGrid(items, kind) {
  var html = '<div class="tcm-grid">';
  items.forEach(function (it) {
    var info = it.info || {};
    var hitChips = "";
    if (it.hits) {
      Object.keys(it.hits).forEach(function (k) {
        (it.hits[k] || []).forEach(function (w) {
          hitChips += '<span class="hit-chip">' + esc(k) + ":" + esc(w) + "</span>";
        });
      });
    }
    html += '<div class="tcm-card">';
    html += '<span class="tcm-stamp ' + kind + '">' + (kind === "full" ? "完全匹配" : "部分匹配") + "</span>";
    html += "<h4>" + esc(cleanName(it.name)) + favStarHerb(cleanName(it.name), it) + "</h4>";
    html += toxBadge(info.toxicity || it.toxicity);
    if (hitChips) html += '<div style="margin-top:4px">' + hitChips + "</div>";
    if (info.property) html += '<div class="detail-line"><span class="k">药性：</span>' + esc(info.property) + "</div>";
    if (info.meridian) html += '<div class="detail-line"><span class="k">归经：</span>' + esc(info.meridian) + "</div>";
    if (info.function) html += '<div class="detail-line"><span class="k">功效：</span>' + esc(info.function) + "</div>";
    html += '<div class="detail-muted">匹配度 ' + esc(it.score) + "</div>";
    html += "</div>";
  });
  html += "</div>";
  return html;
}

/* ============================================================
   模块 3：Grad-CAM → 叠加图 + 透明度滑块
   ============================================================ */
var gradcamOverlayData = null;   // 后端返回的叠加图 dataURL

$("[data-gradcam]").addEventListener("click", async function () {
  var btn = this;
  var imgFile = uploadState.gradcam.file;
  var resultBox = $("#gradcam-result");
  var loading = $(".result-loading", resultBox);
  var stage = $("#gradcam-stage");

  if (!imgFile) { alert("请先上传图片。"); return; }
  resultBox.hidden = false;
  loading.hidden = false;
  stage.hidden = true;
  btn.disabled = true;
  try {
    var fd = new FormData();
    fd.append("image", imgFile);
    fd.append("text", "");
    var resp = await fetch("/explain", { method: "POST", body: fd });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var blob = await resp.blob();
    var infoRaw = resp.headers.get("X-Explain-Info") || "";
    var info = infoRaw ? decodeURIComponent(infoRaw) : "";
    loading.hidden = true;
    stage.hidden = false;
    gradcamOverlayData = URL.createObjectURL(blob);
    renderGradcam(stage, info);
  } catch (err) {
    loading.hidden = true;
    alert("生成失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderGradcam(stage, info) {
  var canvas = $("#gradcam-canvas");
  var alphaInput = $("#gradcam-alpha");
  var alphaVal = $("#gradcam-alpha-val");
  var infoBox = $("#gradcam-info");

  var img = new Image();
  img.onload = function () {
    var W = img.naturalWidth, H = img.naturalHeight;
    var S = Math.max(W, H);
    canvas.width = S; canvas.height = S;
    var ctx = canvas.getContext("2d");

    function draw() {
      var a = alphaInput.value / 100;
      ctx.clearRect(0, 0, S, S);
      ctx.globalAlpha = 1;
      ctx.drawImage(img, 0, 0, S, S);  // 底图（overlay 已含 55% 原图）
      ctx.globalAlpha = a;              // 滑块控制叠加强度
      ctx.drawImage(img, 0, 0, S, S);  // 二次绘制增强热力
      ctx.globalAlpha = 1;
      alphaVal.textContent = alphaInput.value + "%";
    }
    alphaInput.oninput = draw;
    draw();
  };
  img.src = gradcamOverlayData;
  infoBox.innerHTML = mdToHtml(info);
}

/* ============================================================
   模块 4：AI 对话（聊天室）
   ============================================================ */
var chatHistory = [];   // [{role, content}] 发给后端
var chatAttachData = null; // {file, url}

var lastChat = { q: "", a: "", s: [] };   // 最近一条助手回答，供收藏对话使用
var lastChatImgBase64 = null;              // 最近一条对话的附图 base64，供收藏上传

function chatAddMsg(role, contentHtml, extra) {
  var box = $("#chat-history");
  var wrap = document.createElement("div");
  wrap.className = "chat-msg " + role;
  if (extra && extra.imgUrl) {
    var img = document.createElement("img");
    img.className = "msg-img";
    img.src = extra.imgUrl;
    wrap.appendChild(img);
  }
  var div = document.createElement("div");
  div.innerHTML = contentHtml;
  wrap.appendChild(div);
  // 助手回答（非加载/错误态）附加收藏按钮
  if (role === "assistant" && extra && extra.fav) {
    var fav = document.createElement("button");
    fav.type = "button";
    fav.className = "fav-star fav-star-inline";
    fav.textContent = "★";
    fav.title = "收藏该对话";
    var q = extra.fav.q, a = extra.fav.a, s = extra.fav.s || [];
    fav.setAttribute("data-fav-chat", encodeURIComponent(q));
    fav.setAttribute("data-fav-answer", encodeURIComponent(a));
    fav.setAttribute("data-fav-sources", encodeURIComponent(JSON.stringify(s)));
    wrap.appendChild(fav);
  }
  var time = document.createElement("div");
  time.className = "msg-time";
  time.textContent = nowTime();
  wrap.appendChild(time);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return wrap;
}

function chatAttachFile(file) {
  if (!file || !file.type.startsWith("image/")) return;
  if (chatAttachData && chatAttachData.url) URL.revokeObjectURL(chatAttachData.url);
  chatAttachData = { file: file, url: URL.createObjectURL(file), base64: null };
  var reader = new FileReader();
  reader.onload = function (e) { chatAttachData.base64 = e.target.result; };
  reader.readAsDataURL(file);
  var box = $("#chat-attach");
  box.hidden = false;
  $("img", box).src = chatAttachData.url;
}

$("[data-attach]").addEventListener("click", () => $("#chat-file").click());
$("#chat-file").addEventListener("change", function () {
  if (this.files[0]) chatAttachFile(this.files[0]);
});
$("[data-attach-clear]").addEventListener("click", function () {
  chatAttachData = null;
  $("#chat-attach").hidden = true;
  $("#chat-file").value = "";
});

/* ---------- 拖拽 & 粘贴上传（AI 对话） ---------- */
(function initChatDropPaste() {
  var box = $("[data-chat-drop]");
  if (!box) return;

  // 拖拽：在 chatbox 范围内高亮并接收图片
  ["dragenter", "dragover"].forEach(function (ev) {
    box.addEventListener(ev, function (e) {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
      e.preventDefault();
      box.classList.add("drag-over");
    });
  });
  ["dragleave", "dragend"].forEach(function (ev) {
    box.addEventListener(ev, function (e) {
      // 仅当真正离开容器时取消高亮
      if (ev === "dragleave" && box.contains(e.relatedTarget)) return;
      box.classList.remove("drag-over");
    });
  });
  box.addEventListener("drop", function (e) {
    e.preventDefault();
    box.classList.remove("drag-over");
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) chatAttachFile(f);
  });

  // 粘贴：在对话输入框粘贴图片即可附图
  document.addEventListener("paste", function (e) {
    var active = document.activeElement;
    // 仅当焦点在对话区域（输入框或聊天框）时拦截，避免误吞其它粘贴
    if (active && active !== $("#chat-input") && active !== box && !box.contains(active)) return;
    var item = e.clipboardData && e.clipboardData.items && Array.from(e.clipboardData.items)
      .find(function (it) { return it.kind === "file" && it.type.startsWith("image/"); });
    if (!item) return;
    var f = item.getAsFile();
    if (f) {
      e.preventDefault();
      chatAttachFile(f);
    }
  });
})();

$("[data-send]").addEventListener("click", sendChat);
$("#chat-input").addEventListener("keydown", function (e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

async function sendChat() {
  var input = $("#chat-input");
  var question = input.value.trim();
  var btn = $("[data-send]");
  if (!question && !chatAttachData) { alert("请输入问题或上传图片。"); return; }

  chatAddMsg("user", esc(question || (chatAttachData ? "（图片）" : "")),
    chatAttachData ? { imgUrl: chatAttachData.url } : null);
  chatHistory.push({ role: "user", content: question });
  input.value = "";
  btn.disabled = true;

  var tmp = chatAddMsg("assistant", '<span class="spinner" style="display:inline-block;vertical-align:middle"></span> 正在思考……');
  try {
    var fd = new FormData();
    fd.append("question", question);
    if (chatAttachData) fd.append("image", chatAttachData.file);
    fd.append("history", JSON.stringify(chatHistory.slice(0, -1)));
    var data = await postForm("/chat", fd);
    tmp.remove();

    var html = mdToHtml(data.answer || data.message || "（无回答）");
    // 知识库来源折叠
    if (data.rag_sources && data.rag_sources.length) {
      html += '<details class="rag-source"><summary>知识库来源（' + data.rag_sources.length + "）</summary><div class='rag-body'>";
      data.rag_sources.forEach(function (s) {
        html += '<div class="rag-item">' + esc(s.title || s.name || "") + (s.meta ? " · " + esc(s.meta) : "") + "</div>";
      });
      html += "</div></details>";
    }
    lastChat = { q: question, a: data.answer || data.message || "", s: data.rag_sources || [] };
    if (chatAttachData && chatAttachData.file && !chatAttachData.base64) {
      lastChatImgBase64 = await new Promise(function (resolve) {
        var r = new FileReader();
        r.onload = function (e) { resolve(e.target.result); };
        r.readAsDataURL(chatAttachData.file);
      });
    } else {
      lastChatImgBase64 = chatAttachData ? chatAttachData.base64 : null;
    }
    chatAddMsg("assistant", html, { fav: lastChat });
    chatHistory.push({ role: "assistant", content: data.answer || "" });
  } catch (err) {
    tmp.remove();
    chatAddMsg("assistant", '<div class="tcm-risk">请求失败：' + esc(err.message) + "</div>");
  } finally {
    btn.disabled = false;
  }
}

/* ============================================================
   模块 5：药材关系图谱（力导向网络图）
   移植自 app/graph_view.py 的纯 Canvas 力导向实现，改用新中式配色
   ============================================================ */
var GRAPH_CAT_COLORS = {
  "补虚": "#A93226", "清热": "#2C6CB0", "解表": "#5E8C5A", "活血": "#7B4E9E",
  "利水渗湿": "#3D8B8B", "安神": "#B98A2E", "化痰止咳": "#C76A2E",
  "消食": "#8A5A2E", "温里": "#B03A2E", "其他": "#8C8578"
};
var GRAPH_REL_STYLE = {
  "paired":       { color: "#5E8C5A", width: 2.2, dash: [] },
  "incompatible": { color: "#A93226", width: 3.2, dash: [7, 5] },
  "restraint":    { color: "#B98A2E", width: 2.2, dash: [4, 4] },
  "formula_in":   { color: "#7B4E9E", width: 1.8, dash: [2, 3] },
  "category":     { color: "#C9C2B2", width: 1.2, dash: [] },
  "meridian":     { color: "#C9C2B2", width: 1.2, dash: [] }
};
var GRAPH_REL_LABEL = {
  "paired": "相须相使", "incompatible": "十八反", "restraint": "十九畏",
  "formula_in": "组成", "category": "功效分类", "meridian": "归经"
};

var graphData = { nodes: [], links: [] };
var graphNodes = [], graphLinks = [], graphMeta = {};
var graphSelected = null, graphHl = new Set();
var graphZoom = 1, graphPanX = 0, graphPanY = 0;
var graphN = 0, graphRep = 16000;

function graphReset() {
  graphSelected = null; graphHl.clear();
  graphZoom = 1; graphPanX = 0; graphPanY = 0;
}

function graphBuild() {
  graphNodes = graphData.nodes.map(function (d) {
    return {
      id: d.id, type: d.type || "herb", focus: !!d.focus,
      property: d.property || "", meridian: d.meridian || "",
      "function": d["function"] || "", categories: d.categories || [],
      toxicity: d.toxicity || "无毒", aliases: d.aliases || [],
      indications: d.indications || "", cautions: d.cautions || "",
      pairs: d.pairs || [], incompatible: d.incompatible || [],
      restraint: d.restraint || [], source: d.source || "",
      composition_text: d.composition_text || "", effects: d.effects || "",
      warning: d.warning || "", category: d.category || "",
      x: 0, y: 0, vx: 0, vy: 0, fixed: false
    };
  });
  graphLinks = graphData.links.map(function (l) {
    return { source: l.source, target: l.target, relation: l.relation };
  });
  graphMeta = {};
  graphNodes.forEach(function (n) { graphMeta[n.id] = n; });
  graphN = graphNodes.length;
  graphRep = graphN > 120 ? 26000 : 16000;

  // 确定性初始布局：圆环
  var R = Math.sqrt(graphN) * 17 + 40;
  var W = 1040, H = 620;
  graphNodes.forEach(function (n, i) {
    var ang = (i / Math.max(graphN, 1)) * Math.PI * 2;
    n.x = W / 2 + R * Math.cos(ang);
    n.y = H / 2 + R * Math.sin(ang);
  });
}

function graphHerbColor(n) {
  var c = (n.categories && n.categories[0]) || "其他";
  return GRAPH_CAT_COLORS[c] || GRAPH_CAT_COLORS["其他"];
}

function graphSimulate() {
  var W = 1040, H = 620;
  var i, j, a, b, dx, dy, d, f, fx, fy;
  for (i = 0; i < graphN; i++) {
    a = graphNodes[i];
    for (j = i + 1; j < graphN; j++) {
      b = graphNodes[j];
      dx = a.x - b.x; dy = a.y - b.y;
      d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1) { d = 1; dx = 1; dy = 0; }
      f = graphRep / (d * d);
      fx = dx / d * f; fy = dy / d * f;
      if (!a.fixed) { a.vx += fx; a.vy += fy; }
      if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
    }
  }
  for (i = 0; i < graphLinks.length; i++) {
    var l = graphLinks[i];
    a = graphMeta[l.source]; b = graphMeta[l.target];
    if (!a || !b) continue;
    dx = b.x - a.x; dy = b.y - a.y;
    d = Math.sqrt(dx * dx + dy * dy) || 1;
    var rest = (a.type !== "herb" || b.type !== "herb") ? 55 : 96;
    f = 0.028 * (d - rest);
    fx = dx / d * f; fy = dy / d * f;
    if (!a.fixed) { a.vx += fx; a.vy += fy; }
    if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
  }
  for (i = 0; i < graphN; i++) {
    a = graphNodes[i];
    if (a.fixed) continue;
    a.vx += (W / 2 - a.x) * 0.002;
    a.vy += (H / 2 - a.y) * 0.002;
    a.vx *= 0.84; a.vy *= 0.84;
    var sp = Math.sqrt(a.vx * a.vx + a.vy * a.vy);
    if (sp > 10) { a.vx = a.vx / sp * 10; a.vy = a.vy / sp * 10; }
    a.x += a.vx; a.y += a.vy;
    if (a.x < -200) a.x = -200; if (a.x > W + 200) a.x = W + 200;
    if (a.y < -200) a.y = -200; if (a.y > H + 200) a.y = H + 200;
  }
}

function graphUpdateHighlight() {
  graphHl.clear();
  if (graphSelected) {
    graphHl.add(graphSelected.id);
    graphLinks.forEach(function (l) {
      if (l.source === graphSelected.id) graphHl.add(l.target);
      if (l.target === graphSelected.id) graphHl.add(l.source);
    });
  }
}

function graphDraw() {
  var canvas = $("#graph-canvas");
  var W = 1040, H = 620;
  var dpr = window.devicePixelRatio || 1;
  if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
  var ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(graphPanX, graphPanY);
  ctx.scale(graphZoom, graphZoom);

  graphLinks.forEach(function (l) {
    var a = graphMeta[l.source], b = graphMeta[l.target];
    if (!a || !b) return;
    var st = GRAPH_REL_STYLE[l.relation] || GRAPH_REL_STYLE.category;
    var active = !graphSelected || (graphHl.has(a.id) && graphHl.has(b.id));
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

  graphNodes.forEach(function (n) {
    var active = !graphSelected || graphHl.has(n.id);
    ctx.globalAlpha = active ? 1 : 0.13;
    if (n.type === "herb") {
      var r = n.focus ? 16 : 11;
      var tox = n.toxicity || "无毒";
      var toxic = (tox === "大毒" || tox === "有毒");
      if (toxic) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = "#A93226";
        ctx.lineWidth = 2.5;
        ctx.stroke();
        ctx.fillStyle = "#A93226";
        ctx.font = "bold 10px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText("⚠", n.x + r + 5, n.y - r - 2);
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = graphHerbColor(n);
      ctx.fill();
      ctx.lineWidth = n.focus ? 3 : 1.5;
      ctx.strokeStyle = n.focus ? "#1A1915" : "rgba(0,0,0,0.35)";
      ctx.stroke();
      if (graphSelected && graphSelected.id === n.id) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = "#A93226";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.fillStyle = "#fff";
      ctx.font = (n.focus ? "bold 12px" : "11px") + " 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      var label = n.id.length > 4 ? n.id.slice(0, 4) + "…" : n.id;
      ctx.fillText(label, n.x, n.y + 1);
    } else {
      var s = 9;
      ctx.fillStyle = n.type === "category" ? "#8C8578"
        : (n.type === "formula" ? "#7B4E9E" : "#B98A2E");
      ctx.beginPath();
      if (n.type === "category") {
        ctx.rect(n.x - s, n.y - s, s * 2, s * 2);
      } else if (n.type === "formula") {
        ctx.moveTo(n.x, n.y - s);
        ctx.lineTo(n.x + s, n.y);
        ctx.lineTo(n.x, n.y + s);
        ctx.lineTo(n.x - s, n.y);
        ctx.closePath();
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
      ctx.fillStyle = "#6E675E";
      ctx.font = "10px 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(n.id, n.x, n.y + s + 10);
    }
  });
  ctx.restore();
  ctx.globalAlpha = 1;
}

function graphShowDetail(n) {
  var info = $("#graph-info");
  var parts = [];
  if (n.type === "herb") {
    parts.push("<h3>" + esc(cleanName(n.id)) + "</h3>");
    parts.push("<div>药性：" + esc(n.property || "—") + "</div>");
    parts.push("<div>归经：" + esc(n.meridian || "—") + "</div>");
    parts.push("<div>功效：" + esc(n["function"] || "—") + "</div>");
    if (n.aliases && n.aliases.length) parts.push("<div>别名：" + esc(n.aliases.join("、")) + "</div>");
    if (n.indications) parts.push("<div>适用病症：" + esc(n.indications) + "</div>");
    var tox = n.toxicity || "无毒";
    if (tox === "大毒" || tox === "有毒") {
      parts.push('<div class="warn">⚠️ 毒性：' + esc(tox) + "（有毒药材，严禁自行用药）</div>");
    } else if (tox === "小毒" || tox === "微毒") {
      parts.push("<div>毒性：" + esc(tox) + "（含毒性成分，用量需谨慎）</div>");
    } else {
      parts.push("<div>毒性：" + esc(tox) + "</div>");
    }
    if (n.categories && n.categories.length) {
      parts.push("<div>功效分类：" + n.categories.map(function (c) {
        return '<span class="tag" style="background:' + (GRAPH_CAT_COLORS[c] || "#eee") + '22;color:' + (GRAPH_CAT_COLORS[c] || "#666") + '">' + esc(c) + "</span>";
      }).join("") + "</div>");
    }
    parts.push(n.pairs && n.pairs.length
      ? '<div>常用配伍：<span class="ok">' + esc(n.pairs.join("、")) + "</span></div>"
      : '<div>常用配伍：<span class="muted">无</span></div>');
    parts.push(n.incompatible && n.incompatible.length
      ? '<div>十八反：<span class="warn">' + esc(n.incompatible.join("、")) + "</span></div>"
      : '<div>十八反：<span class="muted">无</span></div>');
    parts.push(n.restraint && n.restraint.length
      ? '<div>十九畏：<span class="warn">' + esc(n.restraint.join("、")) + "</span></div>"
      : '<div>十九畏：<span class="muted">无</span></div>');
    if (n.cautions) parts.push('<div class="warn">⚠️ 个体禁忌：' + esc(n.cautions) + "</div>");
    if (n.focus) parts.push('<div class="muted">当前聚焦药材</div>');
  } else if (n.type === "formula") {
    parts.push("<h3>📜 " + esc(n.id) + "</h3>");
    if (n.source) parts.push("<div>出处：" + esc(n.source) + "</div>");
    if (n.category) parts.push("<div>方剂分类：" + esc(n.category) + "</div>");
    if (n.composition_text) parts.push("<div>组成：" + esc(n.composition_text) + "</div>");
    if (n.effects) parts.push("<div>功效：" + esc(n.effects) + "</div>");
    if (n.indications) parts.push("<div>主治：" + esc(n.indications) + "</div>");
    if (n.usage) parts.push("<div>用法：" + esc(n.usage) + "</div>");
    if (n.warning) parts.push('<div class="warn">⚠️ ' + esc(n.warning) + "</div>");
    parts.push('<div class="muted">本图谱仅供科普参考，不构成用药建议。</div>');
  } else {
    parts.push("<h3>" + esc(n.id) + "</h3>");
    parts.push('<div class="muted">与图中相连的药材相关联。</div>');
  }
  info.innerHTML = parts.join("");
  info.style.display = "block";
}

function graphCanvasToWorld(e) {
  var canvas = $("#graph-canvas");
  var rect = canvas.getBoundingClientRect();
  var W = 1040, H = 620;
  var mx = (e.clientX - rect.left) * (W / rect.width);
  var my = (e.clientY - rect.top) * (H / rect.height);
  return { x: (mx - graphPanX) / graphZoom, y: (my - graphPanY) / graphZoom };
}

function graphHitTest(p) {
  for (var i = graphNodes.length - 1; i >= 0; i--) {
    var n = graphNodes[i];
    var r = n.type === "herb" ? (n.focus ? 16 : 11) : 9;
    var dx = n.x - p.x, dy = n.y - p.y;
    if (dx * dx + dy * dy <= (r + 4) * (r + 4)) return n;
  }
  return null;
}

function graphInitEvents() {
  var canvas = $("#graph-canvas");
  var draggingNode = null, panning = false, lastX = 0, lastY = 0;

  canvas.addEventListener("mousedown", function (e) {
    var p = graphCanvasToWorld(e);
    var hit = graphHitTest(p);
    if (hit) {
      draggingNode = hit;
      hit.fixed = true;
      hit.x = p.x; hit.y = p.y;
      canvas.classList.add("dragging");
    } else {
      panning = true;
      lastX = e.clientX; lastY = e.clientY;
      canvas.classList.add("dragging");
      if (graphSelected) {
        graphSelected = null;
        graphUpdateHighlight();
        $("#graph-info").style.display = "none";
      }
    }
  });

  window.addEventListener("mousemove", function (e) {
    var p = graphCanvasToWorld(e);
    if (draggingNode) {
      draggingNode.x = p.x; draggingNode.y = p.y;
    } else if (panning) {
      graphPanX += e.clientX - lastX;
      graphPanY += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
    }
    // 悬浮提示
    var tip = $("#graph-tip");
    if (!draggingNode && !panning) {
      var hit = graphHitTest(p);
      if (hit) {
        tip.textContent = hit.id + (hit.type !== "herb" ? "（" + hit.type + "）" : "");
        tip.style.display = "block";
        tip.style.left = (e.clientX - canvas.getBoundingClientRect().left + 12) + "px";
        tip.style.top = (e.clientY - canvas.getBoundingClientRect().top + 12) + "px";
      } else {
        tip.style.display = "none";
      }
    }
  });

  window.addEventListener("mouseup", function () {
    if (draggingNode) { draggingNode.fixed = false; }
    draggingNode = null;
    panning = false;
    canvas.classList.remove("dragging");
  });

  canvas.addEventListener("click", function (e) {
    var p = graphCanvasToWorld(e);
    var hit = graphHitTest(p);
    if (hit) {
      graphSelected = hit;
      graphUpdateHighlight();
      graphShowDetail(hit);
    }
  });

  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    var factor = e.deltaY < 0 ? 1.1 : 0.9;
    graphZoom = Math.min(3, Math.max(0.4, graphZoom * factor));
  }, { passive: false });
}

function graphBuildLegend() {
  var leg = $("#graph-legend");
  var items = [];
  Object.keys(GRAPH_CAT_COLORS).forEach(function (c) {
    items.push('<span class="item"><span class="dot" style="background:' + GRAPH_CAT_COLORS[c] + '"></span>' + esc(c) + "</span>");
  });
  items.push('<span style="color:var(--ink-soft);margin-left:6px">—— 连线 ——</span>');
  Object.keys(GRAPH_REL_STYLE).forEach(function (r) {
    var st = GRAPH_REL_STYLE[r];
    items.push('<span class="item"><span class="line" style="border-top-color:' + st.color + '"></span>' + (GRAPH_REL_LABEL[r] || r) + "</span>");
  });
  leg.innerHTML = items.join("");
}

function graphLoop() {
  graphSimulate();
  graphDraw();
  requestAnimationFrame(graphLoop);
}

async function loadGraph(focus) {
  var canvas = $("#graph-canvas");
  try {
    var url = "/graph" + (focus ? "?focus=" + encodeURIComponent(focus) : "");
    var resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    graphData = await resp.json();
    if (graphData.categoryColors) {
      Object.assign(GRAPH_CAT_COLORS, graphData.categoryColors);
    }
    graphReset();
    graphBuild();
    graphBuildLegend();
    graphUpdateHighlight();
    if (!window._graphRaf) {
      graphInitEvents();
      window._graphRaf = true;
      graphLoop();
    }
  } catch (err) {
    console.error("图谱加载失败", err);
    var ctx = canvas.getContext("2d");
    ctx.font = "14px sans-serif";
    ctx.fillStyle = "#A93226";
    ctx.fillText("图谱加载失败：" + err.message, 20, 40);
  }
}

/* 图谱工具栏 */
$("[data-graph-load]").addEventListener("click", function () {
  var name = $("#graph-focus").value.trim();
  if (!name) { alert("请输入药材名。"); return; }
  loadGraph(name);
});
$("[data-graph-all]").addEventListener("click", function () {
  $("#graph-focus").value = "";
  loadGraph("");
});
$("#graph-focus").addEventListener("keydown", function (e) {
  if (e.key === "Enter") $("[data-graph-load]").click();
});

/* 药材名补全（datalist） */
async function loadHerbNames() {
  try {
    var resp = await fetch("/herbs");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();
    var list = data.herbs || data.names || [];
    var dl = $("#graph-herbs");
    dl.innerHTML = list.map(function (n) {
      return '<option value="' + esc(n) + '"></option>';
    }).join("");
  } catch (err) {
    console.error("药材名加载失败", err);
  }
}

loadHerbNames();

/* ---------- 从首页跳转激活指定页签（?tab=xxx 或 #tab=xxx） ---------- */
(function activateTabFromUrl() {
  var tabKey = new URLSearchParams(window.location.search).get("tab");
  if (!tabKey) {
    var m = (window.location.hash || "").match(/[#&]tab=([\w-]+)/);
    if (m) tabKey = m[1];
  }
  if (!tabKey) return;
  var tab = $$(".tab").find(function (t) { return t.dataset.tab === tabKey; });
  if (!tab) return;
  // 模拟点击以复用既有切换逻辑（含图谱首次加载）
  tab.click();
  // 定位到工作台顶部，确保用户看到对应界面
  window.scrollTo({ top: 0, behavior: "smooth" });
})();
