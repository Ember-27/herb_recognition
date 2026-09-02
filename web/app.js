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

/** 对 Top-N 列表按药材名去重（忽略括号拼音后缀，如 人参(renshen) -> 人参），保留置信度最高的一条 */
function dedupeTop(list) {
  if (!Array.isArray(list)) return [];
  var seen = {}, out = [];
  list.forEach(function (it) {
    if (!it || !it.name) return;
    var key = cleanName(it.name);
    if (seen[key]) return;
    seen[key] = true;
    out.push(it);
  });
  return out;
}

/**
 * Markdown -> HTML 渲染器（内置，无外部依赖）。
 * 支持：标题(#~######)、有序/无序列表、引用(>)、围栏代码块(```)、
 *       行内代码、表格(|)、加粗、斜体、链接、分割线(---)、段落。
 * 原始文本先经 esc() 转义，避免 LLM 输出导致的 XSS。
 */
function inlineMd(s) {
  return s
    .replace(/`([^`]+?)`/g, (_, c) => "<code>" + c + "</code>")
    .replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+?)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+?)\]\((https?:\/\/[^\s)]+?)\)/g,
             '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function mdToHtml(md) {
  if (!md) return "";
  const lines = String(md).split("\n");
  let html = "", i = 0;
  const state = { list: null, para: [] };
  const closeList = () => { if (state.list) { html += state.list === "ol" ? "</ol>" : "</ul>"; state.list = null; } };
  const flushPara = () => {
    if (state.para.length) {
      html += "<p>" + state.para.map(l => inlineMd(esc(l))).join("<br>") + "</p>";
      state.para = [];
    }
  };
  const tableToHtml = (block) => {
    const rows = block.map(r => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim()));
    let t = '<table class="md-table"><thead><tr>';
    rows[0].forEach(c => t += "<th>" + inlineMd(esc(c)) + "</th>");
    t += "</tr></thead><tbody>";
    rows.slice(2).forEach(r => { t += "<tr>"; r.forEach(c => t += "<td>" + inlineMd(esc(c)) + "</td>"); t += "</tr>"; });
    return t + "</tbody></table>";
  };

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块
    if (/^```/.test(line.trim())) {
      closeList(); flushPara();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) { buf.push(esc(lines[i])); i++; }
      i++;
      html += '<pre class="md-pre"><code>' + buf.join("\n") + "</code></pre>";
      continue;
    }
    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeList(); flushPara();
      const lvl = h[1].length;
      html += `<h${lvl} class="md-h md-h${lvl}">` + inlineMd(esc(h[2])) + `</h${lvl}>`;
      i++; continue;
    }
    // 分割线
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      closeList(); flushPara();
      html += '<hr class="md-hr">';
      i++; continue;
    }
    // 引用
    if (/^>\s?/.test(line)) {
      closeList(); flushPara();
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, "")); i++; }
      html += '<blockquote class="md-quote">' + buf.map(l => inlineMd(esc(l))).join("<br>") + "</blockquote>";
      continue;
    }
    // 表格
    if (/\|/.test(line) && i + 1 < lines.length &&
        /^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
      closeList(); flushPara();
      const buf = [line];
      i++;
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") { buf.push(lines[i]); i++; }
      html += tableToHtml(buf);
      continue;
    }
    // 列表项（含中文•、有序 1. 2)）
    const li = line.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
    if (li) {
      flushPara();
      const tag = /\d/.test(li[1]) ? "ol" : "ul";
      if (state.list !== tag) { closeList(); html += `<${tag} class="md-list">`; state.list = tag; }
      html += "<li>" + inlineMd(esc(li[2])) + "</li>";
      i++; continue;
    }
    // 空行
    if (line.trim() === "") { closeList(); flushPara(); i++; continue; }
    // 普通段落
    state.para.push(line.trim());
    i++;
  }
  closeList(); flushPara();
  // 防御：若解析后无有效 HTML 但原文本非空（解析器可能遇到未知格式），
  // 回退为纯文本逐行显示，避免界面出现完全空白。
  if (!html.trim() && md.toString().trim()) {
    html = "<p>" + esc(md).replace(/\r?\n/g, "<br>") + "</p>";
  }
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
    switchTab(tab.dataset.tab);
  });
});

// 首页 CTA 等任意位置跳转到指定模块（与其它模块平行）
function switchTab(name) {
  var tab = $('[data-tab="' + name + '"]');
  if (!tab) return;
  $$(".tab").forEach(t => { t.classList.remove("active"); t.setAttribute("aria-selected", "false"); });
  $$(".module").forEach(m => m.classList.remove("active"));
  tab.classList.add("active");
  tab.setAttribute("aria-selected", "true");
  $("#" + name).classList.add("active");
  // 图谱模块首次激活时自动加载
  if (name === "graph" && !window._graphLoaded) {
    loadGraph("");
    window._graphLoaded = true;
  }
  // 仅在首页使用亮色页头/导航，其他模块恢复深色
  if (name === "home") document.body.classList.add("home-active");
  else document.body.classList.remove("home-active");
  // 切换页面最底层背景图（在内容之下，不挡交互）
  var bg = document.getElementById("moduleBg");
  if (bg) {
    var O = "rgba(243,230,212,0.74)";   // 浅米遮罩：工作模块照片若隐若现
    var D = "rgba(18,14,12,0.52)";       // 深色遮罩：首页照片压暗沉浸
    var BG = {
      home:      "linear-gradient(" + D + "," + D + "), url('images/_01/chinese-medicine-2178253_1280.jpg')",
      recognize: "linear-gradient(" + O + "," + O + "), url('images/_01/pexels-412104586-39268177.jpg')",
      search:    "linear-gradient(" + O + "," + O + "), url('images/_01/pexels-pietrozj-235494.jpg')",
      gradcam:   "linear-gradient(" + O + "," + O + "), url('images/_01/natural-medicine-1426647_1280.jpg')",
      chat:      "linear-gradient(" + O + "," + O + "), url('images/_01/kian2018-chinese-medicine-3666189_1920.jpg')",
      graph:     "linear-gradient(" + O + "," + O + "), url('images/_01/chinese-medicine-3528232_1280.jpg')"
    };
    bg.style.backgroundImage = BG[name] || "";
  }
}

$$(".home-go").forEach(function (btn) {
  btn.addEventListener("click", function () { switchTab(btn.dataset.go); });
});

// 初始化：首屏只激活「首页」，纠正 HTML 中可能出现的多余 active
switchTab("home");

/* ============================================================
   首页右侧：中药预览照片堆叠（景深轮播）
   多张药材照片错位叠放，当前张清晰、后方逐张缩小模糊下移，
   定时轮换，使后一张从景深里「一张张浮现」。
   ============================================================ */
(function () {
  var HERBS = [
    { img: "images/showcase/chenpi.jpg",    name: "陈皮",   py: "chén pí" },
    { img: "images/showcase/gancao.jpg",    name: "甘草",   py: "gān cǎo" },
    { img: "images/showcase/gouqizi.jpg",   name: "枸杞子", py: "gǒu qǐ zǐ" },
    { img: "images/showcase/honghua.jpg",   name: "红花",   py: "hóng huā" },
    { img: "images/showcase/huangqin.jpg",  name: "黄芩",   py: "huáng qín" },
    { img: "images/showcase/jinyihua.jpg",  name: "金银花", py: "jīn yín huā" },
    { img: "images/showcase/shihu.jpg",     name: "石斛",   py: "shí hú" }
  ];
  var stage = document.getElementById("homePreview");
  if (!stage) return;

  var photos = HERBS.map(function (h, i) {
    var el = document.createElement("div");
    el.className = "home-photo enter";
    var img = document.createElement("img");
    img.src = h.img;
    img.alt = h.name;
    var meta = document.createElement("div");
    meta.className = "home-photo-meta";
    var nm = document.createElement("span");
    nm.className = "home-name";
    nm.textContent = h.name;
    var py = document.createElement("span");
    py.className = "home-py";
    py.textContent = h.py;
    meta.appendChild(nm);
    meta.appendChild(py);
    el.appendChild(img);
    el.appendChild(meta);
    el.title = h.name + " " + h.py;
    el.addEventListener("click", function () {
      // 跳转到「关系图谱」并聚焦该药材
      var ginput = document.getElementById("graph-focus");
      if (ginput) ginput.value = h.name;
      // 先标记图谱已初始化，避免 switchTab 内部的 loadGraph("") 与本次聚焦并发冲突
      window._graphLoaded = true;
      switchTab("graph");
      if (typeof loadGraph === "function") loadGraph(h.name);
    });
    el.addEventListener("animationend", function () { el.classList.remove("enter"); });
    stage.appendChild(el);
    return el;
  });

  // order：当前可见顺序，order[0] 为最前（清晰）那张
  var order = photos.map(function (_, i) { return i; });
  var VISIBLE = 4; // 景深栈中可见张数

  function render() {
    photos.forEach(function (el, i) {
      var p = order.indexOf(i);
      el.classList.remove("pos-0", "pos-1", "pos-2", "pos-3", "pos-hidden", "pos-out");
      el.classList.add(p < VISIBLE ? ("pos-" + p) : "pos-hidden");
    });
  }
  render();

  var timer = null;
  var DELAY = 2800;
  function tick() {
    // 队首移到队尾：原当前张退入景深后方，下一张浮现到最前
    order.push(order.shift());
    render();
  }
  function start() {
    if (timer) return;
    timer = setInterval(tick, DELAY);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  // 仅首页激活时播放，切走时暂停
  var _origSwitch = switchTab;
  window.switchTab = function (name) {
    _origSwitch(name);
    if (name === "home") start(); else stop();
  };
  start();
})();

/* ============================================================
   通用上传组件（拖拽 + 点击 + Ctrl+V 粘贴）
   root: 上传器容器，内含 [data-file-input] [data-drop-zone] [data-clear]
   ============================================================ */
var uploadState = {
  recognize: { images: [], activeId: null, viewId: null, cropMode: false },
  gradcam: { file: null, preview: null },
};

function initUploader(root, key) {
  var input = $("[data-file-input]", root);
  var drop = $("[data-drop-zone]", root);
  var preview = $(".uploader-preview", root);
  var img = $("img", preview);
  var clear = $("[data-clear]", root);

  // 框选相关元素（仅 recognize 模块存在，gradcam 为 null）
  var cropActions = key === "recognize" ? $("#crop-actions") : null;
  var cropOverlay = key === "recognize" ? $("#crop-overlay") : null;

  function exitCrop() {
    if (cropOverlay) {
      cropOverlay.hidden = true;
      var stageEl = cropOverlay.parentElement;
      if (stageEl) {
        stageEl.classList.remove("cropping");
        // 移除已确认的选区痕迹（.crop-mark 直接挂在 stage 上，仅靠清空 cropList 不会消失）
        stageEl.querySelectorAll(".crop-mark").forEach(function (el) { el.remove(); });
      }
    }
    // 清空选区列表 chip 并隐藏"清除选框"按钮（仅在 recognize 模块存在）
    var cropListWrap = $("#crop-list");
    if (cropListWrap) cropListWrap.innerHTML = "";
    var btnClearCrops = $("#btn-crop-clear");
    if (btnClearCrops) btnClearCrops.hidden = true;
    if (cropActions) {
      $("#btn-crop").hidden = false;
      $("#btn-crop-confirm").hidden = true;
      $("#btn-crop-cancel").hidden = true;
      $("#btn-crop-reset").hidden = false;
    }
  }

  function setFile(file) {
    if (!file || !file.type.startsWith("image/")) return;
    uploadState[key].file = file;
    var url = URL.createObjectURL(file);
    uploadState[key].preview = url;
    img.src = url;
    preview.hidden = false;
    drop.style.display = "none";
    if (cropActions) {
      cropActions.hidden = false;
    }
    exitCrop();
    if (typeof renderZoneTexts === "function") renderZoneTexts();
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
    if (cropActions) {
      cropActions.hidden = true;
      exitCrop();
    }
    // 注：实际触发清除的是 [data-clear]（重新上传按钮），$("#btn-crop-reset") 已移除
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

initMultiImage();
initUploader($(".uploader[data-uploader='gradcam']"), "gradcam");

/* ============================================================
   模块 1：多图识别
   uploadState.recognize.images[] 每张图独立（file/preview/cropList/result），
   activeId = 左侧主舞台当前编辑的图片；viewId = 右侧结果区当前查看的图片。
   ============================================================ */
function recEl(id) { return document.getElementById(id); }
var imgUid = 0;

function addImages(fileList) {
  var files = Array.prototype.slice.call(fileList || []);
  if (!files.length) return;
  var multi = recEl("rec-multi");
  if (multi) multi.hidden = false;
  files.forEach(function (file) {
    if (!file || !file.type || !file.type.startsWith("image/")) return;
    uploadState.recognize.images.push({
      id: "img_" + (++imgUid),
      file: file,
      preview: URL.createObjectURL(file),
      name: file.name || "图片",
      cropList: [],
      result: null,
      loading: false,
      error: null
    });
  });
  if (!uploadState.recognize.activeId && uploadState.recognize.images.length) {
    uploadState.recognize.activeId = uploadState.recognize.images[0].id;
  }
  if (!uploadState.recognize.viewId && uploadState.recognize.images.length) {
    uploadState.recognize.viewId = uploadState.recognize.images[0].id;
  }
  renderGallery(); renderMain(); renderResultTabs(); syncIdentifyBtn(); updateEmpty();
}

function getImg(id) {
  return uploadState.recognize.images.filter(function (x) { return x.id === id; })[0] || null;
}

function removeImage(id) {
  var arr = uploadState.recognize.images;
  var idx = arr.findIndex(function (x) { return x.id === id; });
  if (idx < 0) return;
  // 仅清理该图片自身的全部选区/结果，互不影响其它图片
  if (arr[idx].preview) URL.revokeObjectURL(arr[idx].preview);
  arr[idx].cropList = [];
  arr[idx].result = null;
  arr[idx].loading = false;
  arr.splice(idx, 1);
  if (uploadState.recognize.activeId === id) {
    uploadState.recognize.activeId = arr.length ? arr[Math.min(idx, arr.length - 1)].id : null;
  }
  if (uploadState.recognize.viewId === id) {
    uploadState.recognize.viewId = arr.length ? arr[0].id : null;
  }
  if (!arr.length) {
    uploadState.recognize.cropMode = false;
    var m = recEl("rec-multi"); if (m) m.hidden = true;
    var ov = recEl("crop-overlay");
    if (ov) { ov.hidden = true; if (ov.parentElement) ov.parentElement.classList.remove("cropping"); }
  }
  renderGallery(); renderMain(); renderResultTabs(); syncIdentifyBtn(); updateEmpty();
  if (window.renderCropList) window.renderCropList(); // 刷新选区 chips 为当前激活图
}

function setActive(id) {
  uploadState.recognize.activeId = id;
  renderGallery(); renderMain(); renderZoneTexts(); syncIdentifyBtn();
  if (uploadState.recognize.cropMode && id) {
    var ov = recEl("crop-overlay"), st = ov && ov.parentElement;
    if (ov) { ov.hidden = false; if (st) st.classList.add("cropping"); }
  }
}

function renderGallery() {
  var wrap = recEl("rec-gallery");
  if (!wrap) return;
  wrap.innerHTML = "";
  uploadState.recognize.images.forEach(function (img, i) {
    var card = document.createElement("div");
    card.className = "rec-thumb" + (img.id === uploadState.recognize.activeId ? " active" : "");
    card.setAttribute("data-img", img.id);
    card.innerHTML =
      '<img src="' + img.preview + '" alt="">' +
      '<span class="rec-thumb-no">' + (i + 1) + '</span>' +
      (img.result ? '<span class="rec-thumb-dot" title="已识别"></span>' : '') +
      '<button type="button" class="rec-thumb-del" data-del="' + img.id + '" title="移除">×</button>';
    card.addEventListener("click", function (e) {
      if (e.target.closest(".rec-thumb-del")) return;
      setActive(img.id);
    });
    wrap.appendChild(card);
  });
  wrap.querySelectorAll("[data-del]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      removeImage(b.getAttribute("data-del"));
    });
  });
}

function renderMain() {
  var stage = recEl("preview-stage");
  var imgEl = recEl("recognize-preview-img");
  var active = getImg(uploadState.recognize.activeId);
  if (!active) { if (stage) stage.style.display = "none"; return; }
  if (stage) stage.style.display = "";
  imgEl.src = active.preview;
  if (stage) stage.querySelectorAll(".crop-mark").forEach(function (el) { el.remove(); });
  renderCropMarks();
}

function renderCropMarks() {
  var stage = recEl("preview-stage");
  if (!stage) return;
  stage.querySelectorAll(".crop-mark").forEach(function (el) { el.remove(); });
  var a = getImg(uploadState.recognize.activeId);
  if (!a || !a.cropList.length) return;
  a.cropList.forEach(function (c, i) {
    var mark = document.createElement("div");
    mark.className = "crop-mark";
    mark.style.left = c.x + "px"; mark.style.top = c.y + "px";
    mark.style.width = c.w + "px"; mark.style.height = c.h + "px";
    var badge = document.createElement("span");
    badge.className = "crop-mark-no"; badge.textContent = i + 1;
    mark.appendChild(badge);
    stage.appendChild(mark);
  });
}

function renderResultTabs() {
  var tabs = recEl("rec-result-tabs");
  if (!tabs) return;
  var imgs = uploadState.recognize.images;
  if (!imgs.length) { tabs.hidden = true; tabs.innerHTML = ""; return; }
  tabs.hidden = false; tabs.innerHTML = "";
  var label = document.createElement("span");
  label.className = "rec-result-tabs-label";
  label.textContent = "查看图片结果：";
  tabs.appendChild(label);
  imgs.forEach(function (img, i) {
    var t = document.createElement("button");
    t.type = "button";
    t.className = "rec-result-tab" + (img.id === uploadState.recognize.viewId ? " active" : "");
    t.setAttribute("data-view", img.id);
    t.innerHTML = (i + 1) + (img.result ? " ✓" : (img.loading ? " …" : ""));
    t.title = img.name;
    t.addEventListener("click", function () {
      uploadState.recognize.viewId = img.id;
      renderResultTabs(); renderResult();
    });
    tabs.appendChild(t);
  });
}

function updateEmpty() {
  var empty = recEl("recognize-empty");
  if (!empty) return;
  empty.style.display = uploadState.recognize.images.length ? "none" : "";
}

async function identifyImage(imgObj) {
  if (!imgObj) return;
  imgObj.loading = true;
  uploadState.recognize.viewId = imgObj.id;
  renderResultTabs(); showLoading();
  try {
    var data;
    if (imgObj.cropList.length) {
      setActive(imgObj.id);
      var files = [];
      for (var i = 0; i < imgObj.cropList.length; i++) {
        var blob = await window.cropZoneToFile(imgObj.cropList[i]);
        if (!blob) throw new Error("选区 " + (i + 1) + " 裁剪失败");
        files.push(new File([blob], "zone" + i + ".png", { type: "image/png" }));
      }
      var fd = new FormData();
      files.forEach(function (f) { fd.append("images", f); });
      var texts = imgObj.cropList.map(function (c) { return (c.text || "").trim(); });
      fd.append("texts", JSON.stringify(texts));
      var resp = await fetch("/predict_multi", { method: "POST", body: fd });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      data = await resp.json();
      if (data.error) throw new Error(data.message);
      imgObj.result = { multi: true, data: data };
    } else {
      var text = recEl("recognize-text").value.trim();
      var f2 = new FormData();
      f2.append("image", imgObj.file);
      f2.append("text", text);
      var r2 = await fetch("/predict", { method: "POST", body: f2 });
      if (!r2.ok) throw new Error("HTTP " + r2.status);
      data = await r2.json();
      if (data.error) throw new Error(data.message);
      imgObj.result = { multi: false, data: data };
    }
  } catch (err) {
    imgObj.error = err.message;
  } finally {
    imgObj.loading = false;
    renderResultTabs(); hideLoading();
    if (uploadState.recognize.viewId === imgObj.id) renderResult();
  }
}

function showLoading() {
  var box = recEl("recognize-result"); if (!box) return;
  box.hidden = false;
  var loading = box.querySelector(".result-loading"); if (loading) loading.hidden = false;
  var cards = recEl("recognize-cards"); if (cards) cards.innerHTML = "";
  var empty = recEl("recognize-empty"); if (empty) empty.style.display = "none";
}
function hideLoading() {
  var box = recEl("recognize-result"); if (!box) return;
  var loading = box.querySelector(".result-loading"); if (loading) loading.hidden = true;
}

function renderResult() {
  var box = recEl("recognize-result");
  var cards = recEl("recognize-cards");
  if (!box || !cards) return;
  var img = getImg(uploadState.recognize.viewId);
  if (!img) { box.hidden = true; return; }
  box.hidden = false;
  // 仅当存在已识别结果时显示导出按钮
  var expBtn = recEl("btn-recog-export");
  if (expBtn) {
    var anyResult = uploadState.recognize.images.some(function (x) { return x.result; });
    expBtn.hidden = !anyResult;
  }
  if (!img.result) {
    cards.innerHTML = '<div class="rec-view-hint">图片 ' + (uploadState.recognize.images.indexOf(img) + 1) +
      ' 尚未识别，请在左侧点击它并「识别当前图片」。</div>';
    return;
  }
  var res = img.result;
  if (res.multi) renderPredictMulti(res.data); else renderPredict(res.data);
}

/* ============ 图片识别：多图上传器 + 选区（针对指定图片） ============ */
function initMultiImage() {
  var root = $(".uploader[data-uploader='recognize']");
  if (!root) return;
  var input = $("[data-file-input]", root);
  var drop = $("[data-drop-zone]", root);
  var clearBtn = $("[data-clear]", root);
  var addMore = $("[data-add-more]", root);

  input.addEventListener("change", function () { addImages(input.files); input.value = ""; });
  drop.addEventListener("click", function () { input.click(); });
  $$("[data-click-hint]", root).forEach(function (el) {
    el.addEventListener("click", function (e) { e.stopPropagation(); input.click(); });
  });
  if (addMore) addMore.addEventListener("click", function () { input.click(); });
  if (clearBtn) clearBtn.addEventListener("click", function () {
    uploadState.recognize.images.forEach(function (x) { if (x.preview) URL.revokeObjectURL(x.preview); });
    uploadState.recognize.images = [];
    uploadState.recognize.activeId = null;
    uploadState.recognize.viewId = null;
    uploadState.recognize.cropMode = false;
    var m = recEl("rec-multi"); if (m) m.hidden = true;
    var ov = recEl("crop-overlay"); if (ov) { ov.hidden = true; ov.parentElement.classList.remove("cropping"); }
    var ca = recEl("crop-actions"); if (ca) ca.hidden = true;
    var hint = recEl("crop-hint"); if (hint) hint.hidden = true;
    renderGallery(); renderResultTabs(); syncIdentifyBtn(); updateEmpty();
  });

  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("drag-over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("drag-over"); });
  });
  drop.addEventListener("drop", function (e) {
    var fl = e.dataTransfer && e.dataTransfer.files;
    if (fl && fl.length) addImages(fl);
  });

  initCropMulti();
  initIdentify();
}

function initIdentify() {
  var btn = $("[data-identify]");
  var btnAll = $("[data-identify-all]");
  if (btn) btn.addEventListener("click", function () {
    var a = getImg(uploadState.recognize.activeId);
    if (!a) { alert("请先添加图片"); return; }
    identifyImage(a);
  });
  if (btnAll) btnAll.addEventListener("click", async function () {
    var imgs = uploadState.recognize.images;
    if (!imgs.length) { alert("请先添加图片"); return; }
    for (var i = 0; i < imgs.length; i++) { await identifyImage(imgs[i]); }
  });
}

/* 选区针对「当前主舞台图片」。流程：点「选区」进入框选态 → 点左侧某图选定目标
   → 在该图上拖拽框选 → 「添加选区」。每张图独立保存 cropList，互不干扰。 */
function initCropMulti() {
  var img = recEl("recognize-preview-img");
  var overlay = recEl("crop-overlay");
  var rect = recEl("crop-rect");
  var stage = overlay ? overlay.parentElement : null;
  var btnCrop = recEl("btn-crop");
  var btnConfirm = recEl("btn-crop-confirm");
  var btnCancel = recEl("btn-crop-cancel");
  var btnClear = recEl("btn-crop-clear");
  var cropHint = recEl("crop-hint");
  var cropListWrap = recEl("crop-list");
  if (!img || !overlay || !stage) return;

  var drawing = false, startX = 0, startY = 0, sel = null;

  function activeCropList() {
    var a = getImg(uploadState.recognize.activeId);
    return a ? a.cropList : [];
  }
  function renderCropList() {
    if (cropListWrap) {
      cropListWrap.innerHTML = "";
      activeCropList().forEach(function (c, i) {
        var chip = document.createElement("span");
        chip.className = "crop-chip";
        chip.innerHTML = "选区 " + (i + 1) +
          ' <button type="button" class="crop-chip-x" data-crop-del="' + i + '" title="删除该选区">×</button>';
        cropListWrap.appendChild(chip);
      });
      cropListWrap.querySelectorAll("[data-crop-del]").forEach(function (b) {
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          activeCropList().splice(parseInt(b.getAttribute("data-crop-del"), 10), 1);
          renderCropList(); syncIdentifyBtn();
        });
      });
    }
    renderCropMarks(); renderZoneTexts(); syncIdentifyBtn();
  }
  function refreshShades() {
    if (!sel) {
      [".crop-shade-t", ".crop-shade-b", ".crop-shade-l", ".crop-shade-r"].forEach(function (s) {
        var el = $(s, overlay); if (el) el.style.cssText = "";
      });
      return;
    }
    var w = stage.clientWidth, h = stage.clientHeight;
    var t = $(".crop-shade-t", overlay), b = $(".crop-shade-b", overlay);
    var l = $(".crop-shade-l", overlay), r = $(".crop-shade-r", overlay);
    if (t) { t.style.top = "0px"; t.style.left = "0px"; t.style.height = sel.y + "px"; t.style.width = w + "px"; }
    if (b) { b.style.bottom = "0px"; b.style.left = "0px"; b.style.height = (h - sel.y - sel.h) + "px"; b.style.width = w + "px"; }
    if (l) { l.style.top = sel.y + "px"; l.style.left = "0px"; l.style.width = sel.x + "px"; l.style.height = sel.h + "px"; }
    if (r) { r.style.top = sel.y + "px"; r.style.right = "0px"; r.style.width = (w - sel.x - sel.w) + "px"; r.style.height = sel.h + "px"; }
  }
  function drawRect() {
    if (!sel) { rect.style.cssText = "display:none"; return; }
    rect.style.display = "block";
    rect.style.left = sel.x + "px"; rect.style.top = sel.y + "px";
    rect.style.width = sel.w + "px"; rect.style.height = sel.h + "px";
    refreshShades();
  }
  function posInStage(e) {
    var r = stage.getBoundingClientRect();
    var cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    var cy = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
    return { x: Math.max(0, Math.min(cx, r.width)), y: Math.max(0, Math.min(cy, r.height)) };
  }
  function begin(e) {
    if (overlay.hidden) return;
    e.preventDefault(); drawing = true;
    var p = posInStage(e); startX = p.x; startY = p.y;
    sel = { x: p.x, y: p.y, w: 0, h: 0 }; drawRect();
  }
  function move(e) {
    if (!drawing) return; e.preventDefault();
    var p = posInStage(e);
    sel = { x: Math.min(startX, p.x), y: Math.min(startY, p.y), w: Math.abs(p.x - startX), h: Math.abs(p.y - startY) };
    drawRect();
  }
  function end() {
    drawing = false;
    if (sel && (sel.w < 8 || sel.h < 8)) { sel = null; rect.style.cssText = "display:none"; refreshShades(); }
  }
  overlay.addEventListener("mousedown", begin);
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", end);
  overlay.addEventListener("touchstart", begin, { passive: false });
  overlay.addEventListener("touchmove", move, { passive: false });
  overlay.addEventListener("touchend", end);

  btnCrop.addEventListener("click", function () {
    if (!getImg(uploadState.recognize.activeId)) { alert("请先添加一张图片"); return; }
    uploadState.recognize.cropMode = true;
    overlay.hidden = false; stage.classList.add("cropping");
    btnCrop.hidden = true; btnConfirm.hidden = false; btnCancel.hidden = false;
    if (cropHint) cropHint.hidden = false;
  });
  btnCancel.addEventListener("click", function () {
    uploadState.recognize.cropMode = false;
    overlay.hidden = true; stage.classList.remove("cropping");
    sel = null; rect.style.cssText = "display:none"; refreshShades();
    btnCrop.hidden = false; btnConfirm.hidden = true; btnCancel.hidden = true;
    if (cropHint) cropHint.hidden = true;
    if (btnClear) btnClear.hidden = true;
  });
  btnConfirm.addEventListener("click", function () {
    if (!sel || sel.w < 8 || sel.h < 8) { alert("请先框选一个有效区域"); return; }
    // 记录选区在原图中的自然像素坐标，便于导出时精确裁剪
    var nat = null;
    if (img && img.naturalWidth && img.clientWidth) {
      var sx = img.naturalWidth / img.clientWidth;
      var sy = img.naturalHeight / img.clientHeight;
      nat = { x: sel.x * sx, y: sel.y * sy, w: sel.w * sx, h: sel.h * sy };
    }
    activeCropList().push({ x: sel.x, y: sel.y, w: sel.w, h: sel.h, nat: nat });
    renderCropList();
    if (btnClear) btnClear.hidden = false;
    sel = null; rect.style.cssText = "display:none"; refreshShades();
  });
  if (btnClear) btnClear.addEventListener("click", function () {
    activeCropList().length = 0; renderCropList();
  });

  // 暴露给识别流程：把某个选区(显示坐标)裁剪为图片文件（取当前主舞台图片）
  window.cropZoneToFile = function (c) {
    var natW = img.naturalWidth, natH = img.naturalHeight;
    var dispW = img.clientWidth, dispH = img.clientHeight;
    if (!natW || !dispW) return null;
    var sx = c.x / dispW * natW, sy = c.y / dispH * natH;
    var sw = c.w / dispW * natW, sh = c.h / dispH * natH;
    var canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(sw));
    canvas.height = Math.max(1, Math.round(sh));
    canvas.getContext("2d").drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    return new Promise(function (res) { canvas.toBlob(res, "image/png"); });
  };

  // 暴露给删除图片逻辑，刷新当前激活图的选区 chips
  window.renderCropList = renderCropList;

  renderCropList();
}

/* 按当前图片的选区分别给出补充描述框（无选区时恢复单一全局框） */
function renderZoneTexts() {
  var globalBox = recEl("recognize-text-global");
  var zoneBox = recEl("recognize-text-zones");
  if (!globalBox || !zoneBox) return;
  if (!uploadState.recognize.images.length) { globalBox.hidden = false; zoneBox.hidden = true; zoneBox.innerHTML = ""; return; }
  var a = getImg(uploadState.recognize.activeId);
  var list = a ? a.cropList : [];
  if (!list.length) { globalBox.hidden = false; zoneBox.hidden = true; zoneBox.innerHTML = ""; return; }
  globalBox.hidden = true; zoneBox.hidden = false; zoneBox.innerHTML = "";
  list.forEach(function (c, i) {
    if (c.text === undefined) c.text = "";
    var wrap = document.createElement("div");
    wrap.className = "zone-text-row";
    var label = document.createElement("label");
    label.className = "field-label";
    label.textContent = "选区 " + (i + 1) + " 描述";
    label.setAttribute("for", "zone-text-" + i);
    var input = document.createElement("input");
    input.id = "zone-text-" + i;
    input.className = "text-input"; input.type = "text"; input.maxLength = 100;
    input.placeholder = "如：叶片椭圆、表面有细毛、味甘";
    input.value = c.text;
    input.addEventListener("input", function () {
      var act = getImg(uploadState.recognize.activeId);
      if (act) act.cropList[i].text = input.value;
    });
    wrap.appendChild(label); wrap.appendChild(input);
    zoneBox.appendChild(wrap);
  });
}

/* 识别按钮文案随当前图片选区数量动态变化 */
function syncIdentifyBtn() {
  var btn = $("[data-identify]");
  if (!btn) return;
  var a = getImg(uploadState.recognize.activeId);
  var n = a ? a.cropList.length : 0;
  btn.textContent = n > 0 ? ("识别当前图片（" + n + " 选区）") : "识别当前图片";
  // 有图片即展示「选区」操作条（单图/多图均可用框选识别）
  var ca = recEl("crop-actions");
  if (ca) ca.hidden = !uploadState.recognize.images.length;
}

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
      if (activeKey === "recognize") {
        addImages([file]);
      } else if (activeKey === "gradcam") {
        var root = $(".uploader[data-uploader='gradcam']");
        var input = $("[data-file-input]", root);
        var drop = $("[data-drop-zone]", root);
        var preview = $(".uploader-preview", root);
        var imgEl = $("img", preview);
        uploadState.gradcam.file = file;
        uploadState.gradcam.preview = URL.createObjectURL(file);
        imgEl.src = uploadState.gradcam.preview;
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
    renderFavList();
    toast("已移除收藏");
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
    $("#favIconHerb").hidden = favTab !== "herb";
    $("#favIconChat").hidden = favTab === "herb";
    // 药材空态用自定义图案，对话空态用原对话气泡
    var favImg = $("#favEmptyImg");
    if (favImg) favImg.hidden = favTab !== "herb";
    if (favTab === "herb") {
      $("#favEmptyTitle").textContent = "还没有收藏药材";
      $("#favEmptyHint").textContent  = "点击药材卡片上的 ★，把喜欢的本草收进册子";
    } else {
      $("#favEmptyTitle").textContent = "还没有收藏对话";
      $("#favEmptyHint").textContent  = "点击回答旁的 ★，保存你和本草的对话";
    }
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
      e.preventDefault();
      e.stopPropagation();
      if (!it.fid) { toast("该收藏缺少标识，无法删除"); return; }
      apiRemoveFav(it.fid);
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
    html += '<div class="tcm-card tcm-card-top1" data-herb="' + esc(top1.name) + '" style="border-left:4px solid var(--vermilion)">';
    html += '<div class="tcm-card-body">';
    html += "<h4>" + esc(cleanName(top1.name)) + favStarHerb(cleanName(top1.name), top1) + "</h4>";
    html += toxBadge(top1.toxicity);
    if (data.low_confidence) {
      html += '<span class="tcm-badge tcm-badge-warn">置信度较低，建议人工复核</span>';
    }
    html += '<div class="tcm-bar"><span style="width:' + (top1.prob * 100).toFixed(1) + '%"></span></div>';
    html += '<div class="detail-muted">置信度 ' + (top1.prob * 100).toFixed(1) + "%</div>";
    html += "</div></div>";
  }

  if (rest.length) {
    html += '<div class="tcm-section-title">候选（Top-2 ~ 5）</div>';
    html += '<div class="tcm-grid">';
    rest.forEach(function (it) {
      html += '<div class="tcm-card" data-herb="' + esc(it.name) + '">';
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
      html += '<div class="tcm-card" data-herb="' + esc(it.name) + '">';
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
      html += '<div class="tcm-card" data-herb="' + esc(s.name) + '">';
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
  attachHerbThumbs(cards);
}

/* 异步为结果卡片中的药材附上训练/验证集聚类样本图（同名保留同一张） */
function attachHerbThumbs(container) {
  if (!container) return;
  var nodes = container.querySelectorAll('.tcm-card[data-herb]');
  if (!nodes.length) return;
  // 去重收集药材名
  var seen = {}, names = [];
  Array.prototype.forEach.call(nodes, function (n) {
    var nm = n.getAttribute('data-herb');
    if (nm && !seen[nm]) { seen[nm] = true; names.push(nm); }
  });
  if (!names.length) return;
  fetch('/herb_sample_image', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names: names })
  }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (resp) {
      if (!resp || !resp.images) return;
      Array.prototype.forEach.call(nodes, function (n) {
        var nm = n.getAttribute('data-herb');
        var b64 = resp.images[nm];
        if (!b64) return;
        if (n.querySelector('.tcm-thumb')) return; // 已注入，避免重复
        var img = document.createElement('img');
        img.className = 'tcm-thumb';
        img.src = b64;
        img.alt = nm;
        n.insertBefore(img, n.firstChild);
      });
    }).catch(function () { /* 样本图缺失不影响主流程 */ });

  // 点击识别结果卡片 → 跳转到知识图谱并聚焦该药材
  Array.prototype.forEach.call(nodes, function (n) {
    var nm = n.getAttribute('data-herb');
    if (!nm || n.dataset.graphBound) return;
    n.dataset.graphBound = "1";
    n.style.cursor = "pointer";
    n.addEventListener("click", function () {
      $$(".tab").forEach(function (t) {
        if (t.dataset.tab === "graph") t.click();
      });
      $("#graph-focus").value = nm;
      loadGraph(nm);
    });
  });
}

/* 多选区批量识别结果：每个选区一张卡 + 跨区配伍分析 + 联动入口 */
function renderPredictMulti(data) {
  var cards = $("#recognize-cards");
  var html = "";
  var zones = data.zones || [];
  var compat = data.compat || {};

  if (!zones.length) {
    cards.innerHTML = '<div class="tcm-risk">未能识别任何选区。</div>';
    return;
  }

  zones.forEach(function (z, i) {
    if (z.error) {
      html += '<div class="tcm-section-title">选区 ' + (i + 1) + '</div>';
      html += '<div class="tcm-risk">' + esc(z.message || "选区识别失败") + "</div>";
      return;
    }
    var top5 = dedupeTop(z.top5);
    var top1 = top5[0];
    var rest = top5.slice(1);
    html += '<div class="tcm-section-title">选区 ' + (i + 1) + ' · 识别结果</div>';
    if (z.text) {
      html += '<div class="zone-text-echo">📝 补充描述：' + esc(z.text) + "</div>";
    }
    if (top1) {
      html += '<div class="tcm-card tcm-card-top1" data-herb="' + esc(top1.name) + '" style="border-left:4px solid var(--vermilion)">';
      html += '<div class="tcm-card-body">';
      html += "<h4>" + esc(cleanName(top1.name)) + favStarHerb(cleanName(top1.name), top1) + "</h4>";
      html += toxBadge(top1.toxicity);
      if (z.low_confidence) html += '<span class="tcm-badge tcm-badge-warn">置信度较低，建议人工复核</span>';
      html += '<div class="tcm-bar"><span style="width:' + (top1.prob * 100).toFixed(1) + '%"></span></div>';
      html += '<div class="detail-muted">置信度 ' + (top1.prob * 100).toFixed(1) + "%</div>";
      html += "</div></div>";
    }
    if (rest.length) {
      html += '<div class="tcm-grid">';
      rest.forEach(function (it) {
        html += '<div class="tcm-card" data-herb="' + esc(it.name) + '">';
        html += "<h4>" + esc(cleanName(it.name)) + favStarHerb(cleanName(it.name), it) + "</h4>";
        html += toxBadge(it.toxicity);
        html += '<div class="tcm-bar"><span style="width:' + (it.prob * 100).toFixed(1) + '%"></span></div>';
        html += '<div class="detail-muted">' + (it.prob * 100).toFixed(1) + "%</div>";
        html += "</div>";
      });
      html += "</div>";
    }
    if (z.kg_info) {
      html += '<div class="tcm-card">' + mdToHtml(z.kg_info) + "</div>";
    }
    if (z.confusable && z.confusable.peer) {
      html += '<div class="tcm-risk">⚠ 与 <strong>' + esc(cleanName(z.confusable.peer)) + "</strong> 外观相似，请注意鉴别。</div>";
    }
  });

  // 跨区配伍分析（知识图谱：十八反/十九畏/相须相使）
  var pairs = compat.pairs || [];
  if (pairs.length) {
    html += '<div class="tcm-section-title">跨选区配伍分析</div>';
    html += '<div class="tcm-card">';
    pairs.forEach(function (p) {
      var a = esc(cleanName(p.a)), b = esc(cleanName(p.b));
      if (p.relation === "incompatible") {
        html += '<div class="tcm-risk">⚠️ ' + a + ' 与 ' + b + ' 存在十八反配伍禁忌，不建议同用。</div>';
      } else if (p.relation === "restraint") {
        html += '<div class="tcm-risk">⚠️ ' + a + ' 与 ' + b + ' 存在十九畏配伍顾忌，需谨慎同用。</div>';
      } else if (p.relation === "paired") {
        html += '<div class="tcm-ok">✅ ' + a + ' 与 ' + b + ' 为常用相须相使配伍，可以一起使用。</div>';
      }
    });
    html += "</div>";
  }

  // 多药联动入口：一键送去对话分析 / 图谱聚焦
  var herbNames = zones.map(function (z) {
    var t = (z.top5 || [])[0];
    return t ? cleanName(t.name) : "";
  }).filter(Boolean);
  if (herbNames.length >= 1) {
    html += '<div class="multi-actions">';
    html += '<button type="button" class="btn-ghost btn-sm" id="btn-multi-chat">💬 多药对话分析</button>';
    html += '<button type="button" class="btn-ghost btn-sm" id="btn-multi-graph">🕸 图谱聚焦</button>';
    html += "</div>";
  }

  html += '<div class="tcm-disclaimer" style="margin-top:16px">⚠ <strong>医疗风险提示</strong>：以上内容仅供科普与学习参考，不构成医疗诊断或用药建议，请咨询执业中医师或药师。</div>';
  cards.innerHTML = html;

  // 绑定联动按钮
  var chatBtn = $("#btn-multi-chat");
  if (chatBtn) chatBtn.addEventListener("click", function () {
    var q = "请解析以下药材：" + herbNames.join("、") + "。包括各自药性、是否可以配伍同用，以及使用注意。";
    var ta = $("#chat-input");
    if (ta) ta.value = q;
    var chatTab = $('[data-tab="chat"]') || $("#tab-chat");
    if (chatTab) chatTab.click();
    ta && ta.focus();
  });
  var gBtn = $("#btn-multi-graph");
  if (gBtn) gBtn.addEventListener("click", function () {
    // 多药联动：全部识别到的药材一并聚焦到图谱
    var focus = (herbNames.length === 1) ? herbNames[0] : herbNames.slice();
    $("#graph-focus").value = herbNames.join("、");
    $$(".tab").forEach(function (t) {
      if (t.dataset.tab === "graph") t.click();
    });
    loadGraph(focus);
  });

  attachHerbThumbs(cards);
}

/* ============================================================
   模块 2：特性检索
   ============================================================ */
/* 检索示例 chips：点击填入输入框并直接检索 */
document.querySelectorAll("#search-chips .search-chip-eg").forEach(function (chip) {
  chip.addEventListener("click", function () {
    var inp = $("#search-text");
    if (inp) inp.value = chip.getAttribute("data-eg") || "";
    var btn = $("[data-search]");
    if (btn) btn.click();
  });
});

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

var lastSearchData = null;   // 最近一次特性检索结果

function searchResultMarkdown(data) {
  var res = (data && data.result) || {};
  if (res.hint && !(res.full && res.full.length)) return res.hint + "\n";
  var md = "";
  if (res.name_hit) {
    md += "## 按药材名检索\n\n" +
      (res.name_query || []).join("、") + "（共 " + (res.full ? res.full.length : 0) + " 味）\n\n";
    if (res.full && res.full.length) md += "### 检索到药材\n\n" + searchItemsMd(res.full) + "\n";
    return md;
  }
  var parsed = res.parsed || {};
  var condChips = [];
  (parsed.flavor || []).concat(parsed.nature || []).forEach(function (w) { condChips.push(w); });
  (parsed.meridian || []).forEach(function (w) { condChips.push(w); });
  (parsed.function_kws || []).forEach(function (w) { condChips.push(w); });
  md += "## 检索条件\n\n" + (condChips.join("、") || "未解析出有效条件") + "\n\n";
  if (res.full && res.full.length) {
    md += "### 完全匹配（" + res.full.length + "）\n\n" + searchItemsMd(res.full) + "\n";
  }
  if (res.partial && res.partial.length) {
    md += "### 部分匹配（" + res.partial.length + "）\n\n" + searchItemsMd(res.partial) + "\n";
  }
  return md;
}

function searchItemsMd(items) {
  return (items || []).map(function (it) {
    var info = it.info || {};
    var lines = [];
    var name = cleanName(it.name);
    var tox = (info.toxicity || it.toxicity) ? " ⚠毒性药材" : "";
    lines.push("**" + name + "**" + tox);
    var hitWords = [];
    if (it.hits) {
      Object.keys(it.hits).forEach(function (k) {
        (it.hits[k] || []).forEach(function (w) { hitWords.push(k + "：" + w); });
      });
    }
    if (hitWords.length) lines.push("匹配：" + hitWords.join("，"));
    if (info.property) lines.push("药性：" + info.property);
    if (info.meridian) lines.push("归经：" + info.meridian);
    if (info.function) lines.push("功效：" + info.function);
    lines.push("匹配度：" + it.score);
    return "- " + lines.join("　");
  }).join("\n");
}

function openSearchExport() {
  if (!lastSearchData || !lastSearchData.result) { toast("暂无可导出的检索结果"); return; }
  var box = recEl("exportSearch");
  if (box) box.hidden = false;
  var nameEl = recEl("searchExportName");
  if (nameEl) nameEl.value = "";
}

function closeSearchExport() {
  var box = recEl("exportSearch");
  if (box) box.hidden = true;
}

function doSearchExport() {
  if (!lastSearchData || !lastSearchData.result) { toast("暂无可导出的检索结果"); return; }
  var fmt = (document.querySelector("input[name='searchExpFmt']:checked") || {}).value || "markdown";
  var rawName = (recEl("searchExportName").value || "").trim();
  var safeName = rawName.replace(/[\\/:*?"<>|]/g, "").trim() || "本草检索结果导出";
  var title = "本草识鉴 · 特性检索结果";
  var mdBody = searchResultMarkdown(lastSearchData);
  if (fmt === "markdown") {
    var md = "# " + title + "\n\n" + mdBody + "\n";
    downloadBlob(new Blob([md], { type: "text/markdown;charset=utf-8" }), safeName + ".md");
    closeSearchExport();
    return;
  }
  // PDF / Word 复用通用导出端点（检索无图片，images 为空）
  var payload = { title: title, items: [{ heading: "", images: [], text: "", markdown: mdBody }] };
  var url = fmt === "pdf" ? "/api/export_recog_pdf" : "/api/export_recog_docx";
  var fname = fmt === "pdf" ? safeName + ".pdf" : safeName + ".docx";
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(function (r) {
    if (!r.ok) {
      return r.json().then(function (j) { throw new Error(j.error || ("HTTP " + r.status)); },
        function () { throw new Error("HTTP " + r.status); });
    }
    return r.blob();
  }).then(function (blob) {
    downloadBlob(blob, fname);
    closeSearchExport();
  }).catch(function (err) {
    toast("导出失败：" + err.message);
  });
}

function initSearchExport() {
  var btn = recEl("btn-search-export");
  if (btn) btn.addEventListener("click", openSearchExport);
  var doBtn = recEl("searchExportDo");
  if (doBtn) doBtn.addEventListener("click", doSearchExport);
  document.querySelectorAll("[data-search-export-close]").forEach(function (el) {
    el.addEventListener("click", closeSearchExport);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeSearchExport();
  });
}

function renderSearch(data) {
  var resultBox = $("#search-result");
  var summaryEl = $("#search-summary");
  var fullEl = $("#search-full");
  var partialEl = $("#search-partial");
  resultBox.hidden = false;
  lastSearchData = data;
  if (recEl("btn-search-export")) recEl("btn-search-export").hidden = false;
  var res = data.result || {};

  if (res.hint && !(res.full && res.full.length)) {
    summaryEl.hidden = false;
    summaryEl.innerHTML = esc(res.hint);
    fullEl.hidden = true;
    partialEl.hidden = true;
    return;
  }

  // 按药材名直接检索（支持多味）
  if (res.name_hit) {
    summaryEl.hidden = false;
    var nameList = (res.name_query || []).map(function (n) {
      return '<span class="hit-chip">' + esc(n) + "</span>";
    }).join("");
    summaryEl.innerHTML = "按药材名检索：" + nameList +
      "（共 " + (res.full ? res.full.length : 0) + " 味）";
    if (res.full && res.full.length) {
      fullEl.hidden = false;
      fullEl.innerHTML = "<h3>检索到药材</h3>" + searchGrid(res.full, "name");
    } else {
      fullEl.hidden = true;
    }
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
    html += '<span class="tcm-stamp ' + kind + '">' +
      (kind === "full" ? "完全匹配" : kind === "partial" ? "部分匹配" : "按名检索") + "</span>";
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

  var S = 448;  // 画布边长（热力固定 224x224，铺满即可）
  canvas.width = S; canvas.height = S;
  var ctx = canvas.getContext("2d");

  var heatImg = new Image();   // 后端返回的纯热力层
  var baseImg = new Image();   // 本地上传原图
  var baseSrc = uploadState.gradcam.preview;

  function draw() {
    var a = alphaInput.value / 100;   // 0 → 纯原图，1 → 纯热力
    ctx.clearRect(0, 0, S, S);
    ctx.globalAlpha = 1;
    // 1) 原图垫底（contain 等比居中，避免拉伸变形）
    if (baseImg.naturalWidth) {
      var bw = baseImg.naturalWidth, bh = baseImg.naturalHeight;
      var scale = Math.min(S / bw, S / bh);
      var dw = bw * scale, dh = bh * scale;
      ctx.drawImage(baseImg, (S - dw) / 2, (S - dh) / 2, dw, dh);
    }
    // 2) 热力层：滑块控制透明度
    ctx.globalAlpha = a;
    ctx.drawImage(heatImg, 0, 0, S, S);
    ctx.globalAlpha = 1;
    alphaVal.textContent = alphaInput.value + "%";
  }

  function onLoad() {
    if (heatImg.complete && (!baseSrc || baseImg.complete)) draw();
  }

  alphaInput.oninput = draw;
  heatImg.onload = onLoad;
  heatImg.src = gradcamOverlayData;
  if (baseSrc) {
    baseImg.onload = onLoad;
    baseImg.src = baseSrc;
  }
  infoBox.innerHTML = mdToHtml(info);
}

/* ============================================================
   模块 4：AI 对话（聊天室）
   ============================================================ */
var chatHistory = [];   // [{role, content}] 发给后端
var chatAttachData = []; // [{file, url, base64}] 支持多图同时识别
var CHAT_ATTACH_MAX = 6; // 单次最多上传图片数

var lastChat = { q: "", a: "", s: [] };   // 最近一条助手回答，供收藏对话使用
var lastChatImgBase64 = null;              // 最近一条对话的附图 base64，供收藏上传

function chatAddMsg(role, contentHtml, extra) {
  var box = $("#chat-history");
  var wrap = document.createElement("div");
  wrap.className = "chat-msg " + role;
  if (extra) {
    var urls = extra.imgs || (extra.imgUrl ? [extra.imgUrl] : []);
    urls.forEach(function (u) {
      var img = document.createElement("img");
      img.className = "msg-img";
      img.src = u;
      wrap.appendChild(img);
    });
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

function renderChatAttach() {
  var box = $("#chat-attach");
  var list = box.querySelector(".chat-attach-list");
  list.innerHTML = "";
  (chatAttachData || []).forEach(function (item, i) {
    var thumb = document.createElement("div");
    thumb.className = "chat-thumb";
    var img = document.createElement("img");
    img.src = item.url;
    img.alt = "附件 " + (i + 1);
    var x = document.createElement("button");
    x.type = "button";
    x.className = "chat-thumb-x";
    x.textContent = "×";
    x.title = "移除该图片";
    x.setAttribute("data-attach-remove", String(i));
    thumb.appendChild(img);
    thumb.appendChild(x);
    list.appendChild(thumb);
  });
  box.hidden = !chatAttachData || chatAttachData.length === 0;
}

function chatAttachFiles(files) {
  if (!files || !files.length) return;
  chatAttachData = chatAttachData || [];
  Array.prototype.forEach.call(files, function (f) {
    if (!f.type || !f.type.startsWith("image/")) return;
    if (chatAttachData.length >= CHAT_ATTACH_MAX) {
      toast("最多同时上传 " + CHAT_ATTACH_MAX + " 张图片");
      return;
    }
    var item = { file: f, url: URL.createObjectURL(f), base64: null };
    var reader = new FileReader();
    reader.onload = function (e) { item.base64 = e.target.result; };
    reader.readAsDataURL(f);
    chatAttachData.push(item);
  });
  renderChatAttach();
}

$("[data-attach]").addEventListener("click", () => $("#chat-file").click());
$("#chat-file").addEventListener("change", function () {
  chatAttachFiles(this.files);
  this.value = ""; // 允许重复选择同一文件
});
// 委托处理：单个缩略图的 × 移除、以及"清空"
$("#chat-attach").addEventListener("click", function (e) {
  var x = e.target.closest("[data-attach-remove]");
  if (x) {
    var i = parseInt(x.getAttribute("data-attach-remove"), 10);
    if (chatAttachData && chatAttachData[i]) {
      if (chatAttachData[i].url) URL.revokeObjectURL(chatAttachData[i].url);
      chatAttachData.splice(i, 1);
    }
    renderChatAttach();
    return;
  }
  if (e.target.closest("[data-attach-clear]")) {
    chatAttachData.forEach(function (it) { if (it.url) URL.revokeObjectURL(it.url); });
    chatAttachData = [];
    renderChatAttach();
  }
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
    var fs = e.dataTransfer && e.dataTransfer.files;
    // 兜底：部分浏览器 drop 时 files 为空，需从 items 取
    if ((!fs || !fs.length) && e.dataTransfer && e.dataTransfer.items) {
      var picked = [];
      Array.prototype.forEach.call(e.dataTransfer.items, function (it) {
        if (it.kind === "file") {
          var f = it.getAsFile();
          if (f) picked.push(f);
        }
      });
      fs = picked;
    }
    if (fs && fs.length) chatAttachFiles(fs);
  });

  // 在窗口层阻止文件拖拽的默认导航行为，确保 drop 落在本容器而非被浏览器打开
  window.addEventListener("dragover", function (e) {
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
      e.preventDefault();
    }
  });
  window.addEventListener("drop", function (e) {
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")
        && !box.contains(e.target)) {
      e.preventDefault();
    }
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
      chatAttachFiles([f]);
    }
  });
})();

/* 对话快捷提问：点击填入并直接发送 */
document.querySelectorAll(".chat-quick").forEach(function (q) {
  q.addEventListener("click", function () {
    var ta = $("#chat-input");
    if (ta) ta.value = q.getAttribute("data-quick") || "";
    sendChat();
  });
});

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
  if (!question && (!chatAttachData || !chatAttachData.length)) { alert("请输入问题或上传图片。"); return; }

  var attach = chatAttachData && chatAttachData.length ? chatAttachData.slice() : null;
  chatAddMsg("user", esc(question || ("（" + attach.length + " 张图片）")),
    attach ? { imgs: attach.map(function (it) { return it.url; }) } : null);
  chatHistory.push({ role: "user", content: question });
  input.value = "";
  btn.disabled = true;

  var tmp = chatAddMsg("assistant", '<span class="spinner" style="display:inline-block;vertical-align:middle"></span> 正在思考……');
  try {
    var fd = new FormData();
    fd.append("question", question);
    if (attach) attach.forEach(function (it) { fd.append("images", it.file); }); // 多图循环上传
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
    // 收藏接口为单图字段，多图时收藏第一张
    if (attach && attach[0]) {
      lastChatImgBase64 = attach[0].base64 || await new Promise(function (resolve) {
        var r = new FileReader();
        r.onload = function (e) { resolve(e.target.result); };
        r.readAsDataURL(attach[0].file);
      });
    } else {
      lastChatImgBase64 = null;
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
   模块 4 附加：导出对话（Markdown / PDF / 图片）
   数据源为 chatHistory（[{role, content}]），用户可勾选部分导出。
   ============================================================ */
function openExportChat() {
  if (!chatHistory || !chatHistory.length) { toast("暂无对话可导出。"); return; }
  var list = $("#exportChatList");
  list.innerHTML = "";
  chatHistory.forEach(function (m, i) {
    var row = document.createElement("label");
    row.className = "export-item export-item-" + (m.role === "user" ? "user" : "assistant");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "export-cb";
    cb.value = String(i);
    cb.checked = true;
    var txt = (m.role === "user" ? "用户：" : "助手：") + stripHtmlMd(m.content || "");
    if (txt.length > 60) txt = txt.slice(0, 60) + "…";
    var span = document.createElement("span");
    span.className = "export-item-text";
    span.textContent = txt;
    row.appendChild(cb);
    row.appendChild(span);
    list.appendChild(row);
  });
  // 默认 markdown 选中
  var md = document.querySelector('input[name="exportFmt"][value="markdown"]');
  if (md) md.checked = true;
  $("#exportChat").hidden = false;
  $("#favMask") && ($("#favMask").hidden = true);
}
function closeExportChat() { $("#exportChat").hidden = true; }

// 把 markdown 粗略转成纯文本，便于在列表中与图片预览展示
function stripHtmlMd(s) {
  return String(s || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+?)`/g, "$1")
    .replace(/[*_#>]/g, "")
    .replace(/\[([^\]]+?)\]\([^)]+?\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function gatherSelectedChat() {
  var cbs = $("#exportChatList").querySelectorAll(".export-cb");
  var out = [];
  cbs.forEach(function (cb) {
    if (cb.checked) {
      var idx = parseInt(cb.value, 10);
      if (chatHistory[idx]) out.push(chatHistory[idx]);
    }
  });
  return out;
}

function downloadBlob(blob, filename) {
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

function timestampName(ext) {
  var d = new Date();
  var p = function (n) { return String(n).padStart(2, "0"); };
  return "本草对话_" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) +
    "_" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + "." + ext;
}

// 1) Markdown
function exportChatMarkdown(list) {
  var lines = ["# 本草识鉴 · 对话导出", "",
    "> 导出时间：" + new Date().toLocaleString(), ""];
  list.forEach(function (m) {
    lines.push(m.role === "user" ? "## 用户" : "## 助手");
    lines.push("");
    lines.push((m.content || "").trim());
    lines.push("");
  });
  var blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  downloadBlob(blob, timestampName("md"));
}

// 2) 图片（Canvas 绘制对话气泡）
function exportChatImage(list) {
  var dpr = 2, pad = 28, lineH = 26, bubblePad = 16, maxW = 720;
  var font = "15px 'Microsoft YaHei', 'PingFang SC', sans-serif";
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d");
  ctx.font = font;

  // 预排版：把每条消息拆成多行（按像素宽度）
  function wrapText(text, x, maxTextW) {
    var out = [];
    // 中文/英文混合按字断行
    var cur = "";
    for (var ch of text) {
      var test = cur + ch;
      if (ctx.measureText(test).width > maxTextW && cur) {
        out.push(cur);
        cur = ch;
      } else {
        cur = test;
      }
    }
    if (cur) out.push(cur);
    return out.length ? out : [""];
  }

  var items = [];   // {role, lines}
  list.forEach(function (m) {
    var plain = stripHtmlMd(m.content || "");
    if (m.role === "user") plain = "用户：" + plain;
    else plain = "助手：" + plain;
    items.push({ role: m.role, lines: wrapText(plain, 0, maxW - pad * 2 - bubblePad * 2) });
  });

  // 计算高度
  var totalH = pad;
  items.forEach(function (it) {
    totalH += bubblePad * 2 + it.lines.length * lineH;
    totalH += 14; // 间距
  });
  totalH += pad;
  canvas.width = maxW * dpr;
  canvas.height = totalH * dpr;
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#f7f3ea";
  ctx.fillRect(0, 0, maxW, totalH);

  var y = pad;
  items.forEach(function (it) {
    var bubbleH = bubblePad * 2 + it.lines.length * lineH;
    var bx = pad, bw = maxW - pad * 2;
    ctx.fillStyle = it.role === "user" ? "#dfe9d8" : "#ffffff";
    roundRect(ctx, bx, y, bw, bubbleH, 12);
    ctx.fill();
    ctx.strokeStyle = "rgba(176,58,46,.18)";
    ctx.lineWidth = 1;
    roundRect(ctx, bx, y, bw, bubbleH, 12);
    ctx.stroke();
    ctx.fillStyle = "#2b2521";
    ctx.font = font;
    ctx.textBaseline = "top";
    it.lines.forEach(function (ln, k) {
      ctx.fillText(ln, bx + bubblePad, y + bubblePad + k * lineH);
    });
    y += bubbleH + 14;
  });

  canvas.toBlob(function (blob) {
    downloadBlob(blob, timestampName("png"));
  }, "image/png");
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// 3) PDF（请求后端生成文件并直接下载，不调起浏览器打印）
async function exportChatPdf(list) {
  try {
    var resp = await fetch("/api/export_chat_pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(list)
    });
    if (!resp.ok) {
      var err = await resp.json().catch(function () { return {}; });
      toast("PDF 导出失败：" + (err.detail || err.message || resp.status));
      return;
    }
    var blob = await resp.blob();
    downloadBlob(blob, "本草对话导出.pdf");
  } catch (e) {
    toast("PDF 导出出错：" + e.message);
  }
}

// 绑定
function initExportChat() {
  var btn = $("#btn-export-chat");
  if (btn) btn.addEventListener("click", openExportChat);
  document.querySelectorAll("[data-export-close]").forEach(function (b) {
    b.addEventListener("click", closeExportChat);
  });
  var all = document.querySelector("[data-export-all]");
  if (all) all.addEventListener("click", function () {
    $("#exportChatList").querySelectorAll(".export-cb").forEach(function (c) { c.checked = true; });
  });
  var none = document.querySelector("[data-export-none]");
  if (none) none.addEventListener("click", function () {
    $("#exportChatList").querySelectorAll(".export-cb").forEach(function (c) { c.checked = false; });
  });
  var doBtn = $("#exportChatDo");
  if (doBtn) doBtn.addEventListener("click", function () {
    var sel = gatherSelectedChat();
    if (!sel.length) { toast("请至少勾选一条对话。"); return; }
    var fmt = (document.querySelector('input[name="exportFmt"]:checked') || {}).value || "markdown";
    if (fmt === "markdown") exportChatMarkdown(sel);
    else if (fmt === "image") exportChatImage(sel);
    else if (fmt === "pdf") exportChatPdf(sel);
    closeExportChat();
    toast("已开始导出（" + fmt + "）。");
  });
  // 点击遮罩关闭
  $("#exportChat").addEventListener("click", function (e) {
    if (e.target === $("#exportChat")) closeExportChat();
  });
}
initExportChat();

/* ============================================================
   模块 1（续）：图片识别结果导出（Markdown / PDF / Word）
   - 手动勾选某张图片（整图，含原图+各分区）或某个选区
   - 每条导出含：原图/分区图、文字描述（若有）、识别结果文字
   ============================================================ */
function fileToDataURL(file) {
  return new Promise(function (res, rej) {
    var fr = new FileReader();
    fr.onload = function () { res(fr.result); };
    fr.onerror = function () { rej(fr.error || new Error("读取文件失败")); };
    fr.readAsDataURL(file);
  });
}

function cropToDataURL(imgObj, c) {
  return fileToDataURL(imgObj.file).then(function (src) {
    return new Promise(function (res, rej) {
      var im = new Image();
      im.onload = function () {
        var nat = c.nat;
        var dx, dy, dw, dh;
        if (nat) { dx = nat.x; dy = nat.y; dw = nat.w; dh = nat.h; }
        else { dx = c.x; dy = c.y; dw = c.w; dh = c.h; } // 无自然坐标则退回显示坐标（1:1）
        dx = Math.max(0, Math.min(dx, im.naturalWidth));
        dy = Math.max(0, Math.min(dy, im.naturalHeight));
        dw = Math.max(1, Math.min(Math.round(dw), Math.max(1, im.naturalWidth - dx)));
        dh = Math.max(1, Math.min(Math.round(dh), Math.max(1, im.naturalHeight - dy)));
        var canvas = document.createElement("canvas");
        canvas.width = dw; canvas.height = dh;
        canvas.getContext("2d").drawImage(im, dx, dy, dw, dh, 0, 0, dw, dh);
        res(canvas.toDataURL("image/png"));
      };
      im.onerror = function () { rej(new Error("裁剪失败")); };
      im.src = src;
    });
  });
}

function recogTop5Md(top5, lowConfidence) {
  if (!top5 || !top5.length) return "_（无候选结果）_\n";
  var lines = top5.map(function (c, i) {
    var prob = (c.prob != null) ? (c.prob * 100).toFixed(1) + "%" : "—";
    var tox = c.toxicity ? " ⚠毒性药材" : "";
    return (i + 1) + ". **" + (c.name || "未知") + "**" + tox + "　置信度 " + prob;
  });
  if (lowConfidence) lines.push("\n> ⚠ 模型对该图置信度较低，结果仅供参考。");
  return lines.join("\n") + "\n";
}

function recogConfusableMd(confusable) {
  if (!confusable || !confusable.peer) return "_（无）_\n";
  var s = "与 **" + confusable.peer + "** 外观相似";
  if (confusable.reason) s += "：" + confusable.reason;
  else s += "。";
  return s + "\n";
}

function recogImageMarkdown(im) {
  // 仅用于「无选区」的单图识别结果；含选区的图片由 buildRecogItems 拆分为原图 + 各选区分别导出
  var res = im.result;
  if (!res) return "";
  var d = res.data || {};
  var t1 = (d.top5 && d.top5[0]) || {};
  var parts = ["## 识别结果\n"];
  parts.push("**最可能：** " + (t1.name || "未知") +
    (t1.prob != null ? "（置信度 " + (t1.prob * 100).toFixed(1) + "%）" : "") +
    (t1.toxicity ? " ⚠毒性药材" : ""));
  parts.push("\n### 候选药材（Top-5）\n" + recogTop5Md(d.top5, d.low_confidence));
  parts.push("\n### 药性详情\n" + (d.kg_info || "_（暂无）_"));
  parts.push("\n### 易混淆药材\n" + recogConfusableMd(d.confusable));
  parts.push("");
  return parts.join("\n");
}

function recogCropMarkdown(im, ci) {
  var res = im.result;
  if (!res || !res.multi) return "";
  var z = (res.data.zones || [])[ci];
  if (!z) return "";
  var parts = ["## 选区 " + (ci + 1) + " · 识别结果\n"];
  if (z.text) parts.push("> 补充描述：" + z.text + "\n");
  var top1 = (z.top5 && z.top5[0]) || {};
  parts.push("**最可能：** " + (top1.name || "未知") +
    (top1.prob != null ? "（置信度 " + (top1.prob * 100).toFixed(1) + "%）" : "") +
    (top1.toxicity ? " ⚠毒性药材" : ""));
  parts.push("\n### 候选药材（Top-5）\n" + recogTop5Md(z.top5, z.low_confidence));
  parts.push("\n### 药性详情\n" + (z.kg_info || "_（暂无）_"));
  parts.push("\n### 易混淆药材\n" + recogConfusableMd(z.confusable));
  parts.push("");
  return parts.join("\n");
}

var recogExportMap = {}; // id -> {kind, imgId, cropIndex}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function openRecogExport() {
  var list = recEl("exportRecogList");
  if (!list) return;
  list.innerHTML = "";
  var nameEl = recEl("recogExportName");
  if (nameEl) nameEl.value = "";
  recogExportMap = {};
  var any = false;
  uploadState.recognize.images.forEach(function (im, gi) {
    if (!im.result) return;
    any = true;
    var imgLabel = "图片 " + (gi + 1) + (im.name ? "（" + im.name + "）" : "");
    var idImg = "rexp-img-" + im.id;
    recogExportMap[idImg] = { kind: "image", imgId: im.id };
    var row = document.createElement("label");
    row.className = "export-chat-item";
    row.innerHTML = '<input type="checkbox" class="export-cb" value="' + idImg + '"> ' +
      '<span>' + escapeHtml(imgLabel) + '（整图 + 各分区）</span>';
    list.appendChild(row);
    if (im.result.multi && im.cropList && im.cropList.length) {
      im.cropList.forEach(function (c, ci) {
        var idc = "rexp-crop-" + im.id + "-" + ci;
        recogExportMap[idc] = { kind: "crop", imgId: im.id, cropIndex: ci };
        var cRow = document.createElement("label");
        cRow.className = "export-chat-item export-chat-item-sub";
        cRow.innerHTML = '<input type="checkbox" class="export-cb" value="' + idc + '"> ' +
          '<span>　选区 ' + (ci + 1) + (c.text ? "（含描述）" : "") + '</span>';
        list.appendChild(cRow);
      });
    }
  });
  if (!any) { toast("暂无可导出的识别结果"); return; }
  recEl("exportRecog").hidden = false;
}

function closeRecogExport() { var m = recEl("exportRecog"); if (m) m.hidden = true; }

function downloadBlob(blob, filename) {
  var u = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = u; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(u); }, 1000);
}

function buildRecogItems() {
  var list = recEl("exportRecogList");
  if (!list) return null;
  var cbs = list.querySelectorAll("input.export-cb:checked");
  if (!cbs.length) { toast("请至少选择一项"); return null; }
  var items = [];
  var chain = Promise.resolve();
  cbs.forEach(function (cb) {
    var m = recogExportMap[cb.value];
    if (!m) return;
    var im = getImg(m.imgId);
    if (!im || !im.result) return;
    var gi = uploadState.recognize.images.indexOf(im) + 1;
    var giLabel = "图片 " + gi + (im.name ? "（" + im.name + "）" : "");
    if (m.kind === "image") {
      var imText = (im.text || "").trim();
      if (im.result.multi && im.cropList && im.cropList.length) {
        // 含选区的图片：先放原图，再分别给出每个选区的结果
        chain = chain.then(function () {
          return fileToDataURL(im.file);
        }).then(function (origURL) {
          var origMd = imText ? "" : "_（整图，以下为各选区分别识别的结果）_\n";
          var compat = (im.result.data && im.result.data.compat) || {};
          if (compat && Array.isArray(compat.pairs) && compat.pairs.length) {
            origMd += "\n### 配伍参考（基于各选区药材）\n";
            compat.pairs.forEach(function (p) {
              var a = p.a || p[0] || "", b = p.b || p[1] || "", rel = p.relation || p[2] || "";
              origMd += "- **" + a + "** ↔ **" + b + "**：" + rel + "\n";
            });
          }
          items.push({
            heading: giLabel + " · 原图",
            images: [origURL],
            text: imText,
            markdown: origMd
          });
        });
        im.cropList.forEach(function (c, ci) {
          chain = chain.then(function () {
            return cropToDataURL(im, c);
          }).then(function (cropURL) {
            items.push({
              heading: giLabel + " · 选区 " + (ci + 1),
              images: [cropURL],
              text: "",
              markdown: recogCropMarkdown(im, ci)
            });
          });
        });
      } else {
        chain = chain.then(function () {
          return fileToDataURL(im.file);
        }).then(function (dataURL) {
          items.push({
            heading: giLabel,
            images: [dataURL],
            text: imText,
            markdown: recogImageMarkdown(im)
          });
        });
      }
    } else {
      chain = chain.then(function () {
        return cropToDataURL(im, im.cropList[m.cropIndex]);
      }).then(function (dataURL) {
        var c = im.cropList[m.cropIndex];
        items.push({
          heading: "图片 " + gi + " · 选区 " + (m.cropIndex + 1),
          images: [dataURL],
          text: "",
          markdown: recogCropMarkdown(im, m.cropIndex)
        });
      });
    }
  });
  return chain.then(function () { return items; });
}

function doRecogExport() {
  var fmt = (document.querySelector("input[name='recogExpFmt']:checked") || {}).value || "markdown";
  var itemsP = buildRecogItems();
  if (!itemsP) return;
  itemsP.then(function (items) {
    if (!items || !items.length) { toast("请至少选择一项"); return; }
    // 用户自定义文件名（去除非法字符，缺省回退到默认名）
    var rawName = (recEl("recogExportName").value || "").trim();
    var safeName = rawName.replace(/[\\/:*?"<>|]/g, "").trim();
    if (!safeName) safeName = "本草识别结果导出";
    if (fmt === "markdown") {
      var md = "# 本草识鉴 · 图片识别结果\n\n";
      items.forEach(function (it) {
        md += "## " + it.heading + "\n\n";
        (it.images || []).forEach(function (src, k) {
          md += "![" + it.heading + " 图" + (k + 1) + "](" + src + ")\n\n";
        });
        if (it.text) md += "> " + it.text + "\n\n";
        md += it.markdown + "\n\n---\n\n";
      });
      downloadBlob(new Blob([md], { type: "text/markdown;charset=utf-8" }), safeName + ".md");
      closeRecogExport();
      return;
    }
    var payload = { title: "本草识鉴 · 图片识别结果", items: items };
    var url = fmt === "pdf" ? "/api/export_recog_pdf" : "/api/export_recog_docx";
    var fname = fmt === "pdf" ? safeName + ".pdf" : safeName + ".docx";
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (j) { throw new Error(j.error || ("HTTP " + r.status)); },
          function () { throw new Error("HTTP " + r.status); });
      }
      return r.blob();
    }).then(function (blob) {
      downloadBlob(blob, fname);
      closeRecogExport();
    }).catch(function (err) {
      toast("导出失败：" + err.message);
    });
  });
}

function initRecogExport() {
  var btn = recEl("btn-recog-export");
  if (btn) btn.addEventListener("click", openRecogExport);
  document.querySelectorAll("[data-recog-export-close]").forEach(function (b) {
    b.addEventListener("click", closeRecogExport);
  });
  var all = document.querySelector("[data-recog-export-all]");
  if (all) all.addEventListener("click", function () {
    recEl("exportRecogList").querySelectorAll(".export-cb").forEach(function (c) { c.checked = true; });
  });
  var none = document.querySelector("[data-recog-export-none]");
  if (none) none.addEventListener("click", function () {
    recEl("exportRecogList").querySelectorAll(".export-cb").forEach(function (c) { c.checked = false; });
  });
  var doBtn = recEl("recogExportDo");
  if (doBtn) doBtn.addEventListener("click", doRecogExport);
  var modal = recEl("exportRecog");
  if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeRecogExport(); });
}
initRecogExport();
initSearchExport();
initUserHerb();

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
var graphDataAll = null;                // 全体药材全图（每次加载全图时更新），筛选始终基于全体药材；点空白也回到此全图
var graphNodes = [], graphLinks = [], graphMeta = {};
var graphSelected = null, graphHl = new Set();
// 多条件叠加筛选：各维度之间为 AND，同一维度多个值为 OR
// categories: 功效分类(可多选)  property: 药性  toxicity: 毒性  meridian: 归经
var graphFilters = { categories: [], property: [], toxicity: [], meridian: [] };
var graphFiltersBak = null;           // 点药材查看关联时暂存当前筛选，点空白再恢复
var graphFilterLocked = false;        // 药材关联视图中屏蔽条件选择（图例禁用），点空白恢复
var graphFilterMode = "and";          // "and"=交集(满足所有维度)  "or"=并集(满足任一维度)

// —— 药性/毒性/归经 内部归类定义（仅影响筛选图例，不改数据）——
var GRAPH_FLAVORS = ["甘", "苦", "辛", "酸", "咸", "淡", "涩"];   // 五味
var GRAPH_NATURES = ["寒", "热", "温", "凉", "平"];               // 四气
var GRAPH_DEGREES = ["微", "大"];                                  // 程度（苦+微 = 微苦）
var GRAPH_MERIDIANS = ["心包", "三焦", "大肠", "小肠", "膀胱", "肝", "心", "脾", "肺", "肾", "胃", "胆"]; // 十二正经
var GRAPH_MER_SYSTEMS = [  // 归经 → 脏腑系统（表里对）
  { label: "肝胆",      vals: ["肝", "胆"] },
  { label: "心·小肠",   vals: ["心", "小肠"] },
  { label: "脾胃",      vals: ["脾", "胃"] },
  { label: "肺·大肠",   vals: ["肺", "大肠"] },
  { label: "肾·膀胱",   vals: ["肾", "膀胱"] },
  { label: "心包·三焦", vals: ["心包", "三焦"] }
];
var GRAPH_TOX_GROUPS = [   // 毒性 → 程度归类
  { label: "无毒", vals: ["无毒"] },
  { label: "低毒", vals: ["小毒", "微毒"] },
  { label: "高毒", vals: ["有毒", "大毒"] }
];

// 从归经文本拆出所含十二经（独立子串匹配，兼容"心包""三焦"）
function graphMeridians(text) {
  var out = [], t = text || "";
  GRAPH_MERIDIANS.forEach(function (m) { if (t.indexOf(m) !== -1) out.push(m); });
  return out;
}
// 基于全体药材统计命中数（图例按钮徽标），锁定态也用全体数据
function graphLegendHitCount(matchFn) {
  var nodes = (graphDataAll && graphDataAll.nodes && graphDataAll.nodes.length) ? graphDataAll.nodes : graphNodes;
  return nodes.filter(function (n) { return n.type === "herb" && matchFn(n); }).length;
}
var graphZoom = 1, graphPanX = 0, graphPanY = 0;
var graphPulse = 0;                  // 聚焦节点光环脉动计时
var GRAPH_FOCUS_COLOR = "#D4A017";  // 聚焦药材金色
var graphViewLock = false;           // 视图锁定：焦点节点钉在屏幕正中
var graphSimEnabled = true;          // 力导向开关：筛选/选中后冻结，使 fit 视图稳定不漂出
var graphN = 0, graphRep = 16000;

function graphReset() {
  graphSelected = null; graphHl.clear();
  graphFilters = { categories: [], property: [], toxicity: [], meridian: [] };
  graphZoom = 1; graphPanX = 0; graphPanY = 0;
  graphPulse = 0; graphViewLock = false;
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

  // 确定性初始布局：圆环；聚焦药材分布在中心内圈，其一阶邻居放外圈
  var R = Math.sqrt(graphN) * 17 + 40;
  var W = 1040, H = 620;
  var focusNodes = graphNodes.filter(function (n) { return n.focus; });
  var focusIds = focusNodes.map(function (n) { return n.id; });
  var focusSet = new Set(focusIds);
  var focusNeighbors = new Set();
  focusIds.forEach(function (fid) {
    graphLinks.forEach(function (l) {
      if (l.source === fid) focusNeighbors.add(l.target);
      if (l.target === fid) focusNeighbors.add(l.source);
    });
  });
  var nf = focusNodes.length;
  graphNodes.forEach(function (n, i) {
    var ang, ringR;
    if (focusSet.has(n.id)) {
      // 多味聚焦：沿中心内圈均匀排布，避免重叠
      var idx = focusIds.indexOf(n.id);
      ang = (idx / Math.max(nf, 1)) * Math.PI * 2 - Math.PI / 2;
      ringR = nf > 1 ? R * 0.28 : 0;
    } else if (focusSet.size && focusNeighbors.has(n.id)) {
      ang = (i / Math.max(graphN, 1)) * Math.PI * 2;
      ringR = R * 0.55;
    } else {
      ang = (i / Math.max(graphN, 1)) * Math.PI * 2;
      ringR = R;
    }
    n.x = W / 2 + ringR * Math.cos(ang);
    n.y = H / 2 + ringR * Math.sin(ang);
  });
  // 单味聚焦：钉死中心；多味：放开以便力导向自然展开，互不重叠
  if (nf === 1) {
    focusNodes[0].x = W / 2; focusNodes[0].y = H / 2;
    focusNodes[0].fixed = true;
  }
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
  // 视图锁定初始化：单味聚焦把焦点钉在画布正中；多味聚焦让各焦点沿初始
  // 内圈分布自由展开，并把视图平移到所有焦点的质心（仅初始化做一次，
  // 之后不再每帧覆盖 pan，允许用户自由拖拽浏览）。
  if (graphViewLock) {
    var foci = [];
    for (i = 0; i < graphN; i++) {
      if (graphNodes[i].focus) foci.push(graphNodes[i]);
    }
    if (foci.length === 1) {
      foci[0].x = W / 2; foci[0].y = H / 2; foci[0].vx = 0; foci[0].vy = 0; foci[0].fixed = true;
      graphPanX = 0; graphPanY = 0;
    } else if (foci.length > 1) {
      var cx = 0, cy = 0;
      foci.forEach(function (f) { cx += f.x; cy += f.y; });
      cx /= foci.length; cy /= foci.length;
      graphPanX = W / 2 - cx * graphZoom;
      graphPanY = H / 2 - cy * graphZoom;
    }
  }
}

// 判断单节点在某一维度上是否命中。
// 同维度多值也遵循全局模式 graphFilterMode：
//   and → 节点需命中该维度的【所有】已选值（如同时具多个分类）
//   or  → 命中该维度的【任一】已选值即可
function graphDimHit(n, dim, vals) {
  if (!vals.length) return false;
  var nodeVals;
  if (dim === "categories") {
    nodeVals = n.categories || [];
  } else if (dim === "property") {
    // 内部归类：子串匹配。选中"苦"→命中 苦寒/微苦/辛苦温…；选"苦"+"微"(交集)→微苦系(苦的程度)
    var pt = n.property || "";
    var hitCount = vals.filter(function (v) { return pt.indexOf(v) !== -1; }).length;
    return graphFilterMode === "or" ? hitCount > 0 : hitCount === vals.length;
  } else if (dim === "toxicity") {
    nodeVals = [n.toxicity || "无毒"];
  } else if (dim === "meridian") {
    nodeVals = graphMeridians(n.meridian);
  } else {
    return false;
  }
  var hitCount = vals.filter(function (v) { return nodeVals.indexOf(v) !== -1; }).length;
  return graphFilterMode === "or" ? hitCount > 0 : hitCount === vals.length;
}

// 判断某节点是否满足当前多条件筛选
//  graphFilterMode === "and"：需命中所有已激活维度（交集）
//  graphFilterMode === "or" ：命中任一已激活维度即可（并集）
//  注：维度之内（如同选多个分类）同样遵循上述模式。
function graphNodeMatch(n) {
  if (!graphHasFilter()) return true;
  var f = graphFilters;
  var dims = [
    ["categories", f.categories],
    ["property", f.property],
    ["toxicity", f.toxicity],
    ["meridian", f.meridian]
  ].filter(function (d) { return d[1].length; });
  if (!dims.length) return true;
  var dimHits = dims.map(function (d) { return graphDimHit(n, d[0], d[1]); });
  return graphFilterMode === "or"
    ? dimHits.some(Boolean)
    : dimHits.every(Boolean);
}

// 当前是否存在任意生效的筛选条件
function graphHasFilter() {
  var f = graphFilters;
  return !!(f.categories.length || f.property.length || f.toxicity.length || f.meridian.length);
}

// 确保当前图数据为「全体药材全图」：当处于某药材子图（点击药材后的关联视图）
// 时，先切回全体药材再应用筛选，使每次筛选范围都是全体药材而非子图。
// 保留已设置的筛选条件 graphFilters，仅还原底层图数据。
function graphEnsureAll() {
  if (graphDataAll && graphData !== graphDataAll) {
    graphData = graphDataAll;
    graphSelected = null;     // 离开药材关联视图，筛选基于全体药材
    graphBuild();
    graphBuildLegend();
  }
  // 回到全体药材即清空聚焦搜索栏，进行全局（按条件）搜索，而非限定某药材
  var gf = document.getElementById("graph-focus");
  if (gf && gf.value) gf.value = "";
}

// 计算某节点当前可见层级（仅在有筛选或选中时区分）
//  返回 "hi" 高亮(选中关联网) | "dim" 虚化(命中筛选但无关) | "hide" 隐藏
function graphNodeLevel(n) {
  if (graphSelected) {
    // 选中态：只看与该药材相关的图谱（同搜索结果），选中节点及其一阶邻居高亮，
    // 其余直接隐藏；不在关联药材中再套用当前筛选条件（不再虚化命中筛选的节点）。
    return graphHl.has(n.id) ? "hi" : "hide";
  }
  if (graphHasFilter()) {
    return graphNodeMatch(n) ? "hi" : "hide";
  }
  return "hi";  // 无筛选无选中：全部正常显示
}

function graphUpdateHighlight() {
  graphHl.clear();
  var key = graphSelected || null;

  // 基础集合：筛选命中（无选中时也作为高亮集）
  if (graphHasFilter() && !key) {
    graphNodes.forEach(function (n) {
      if (n.type === "herb" && graphNodeMatch(n)) graphHl.add(n.id);
    });
    graphLinks.forEach(function (l) {
      var a = graphMeta[l.source], b = graphMeta[l.target];
      if (!a || !b) return;
      var aHit = a.type !== "herb" || graphNodeMatch(a);
      var bHit = b.type !== "herb" || graphNodeMatch(b);
      if (graphFilterMode === "or") {
        if ((a.type === "herb" && graphNodeMatch(a)) || (b.type === "herb" && graphNodeMatch(b))) {
          graphHl.add(a.id); graphHl.add(b.id);
        }
      } else if (aHit && bHit) {
        graphHl.add(a.id); graphHl.add(b.id);
      }
    });
  }

  if (!key) {
    // 无选中无筛选：高亮聚焦节点及其一阶邻居
    if (!graphHasFilter()) {
      var foci = graphNodes.filter(function (n) { return n.focus; });
      if (foci.length) {
        foci.forEach(function (f) {
          graphHl.add(f.id);
          graphLinks.forEach(function (l) {
            if (l.source === f.id) graphHl.add(l.target);
            if (l.target === f.id) graphHl.add(l.source);
          });
        });
      }
    }
    return;
  }

  // 已选中某节点：高亮集合 = 选中节点 + 其一阶邻居（关系网）；其余由 graphNodeLevel 决定虚化/隐藏
  graphHl.add(key.id);
  graphLinks.forEach(function (l) {
    if (l.source === key.id) graphHl.add(l.target);
    if (l.target === key.id) graphHl.add(l.source);
  });
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
    var la = graphNodeLevel(a), lb = graphNodeLevel(b);
    var alpha;
    if (la === "hi" && lb === "hi") alpha = 0.85;              // 关联网：高亮
    else if ((la === "hi" || la === "dim") && (lb === "hi" || lb === "dim")) alpha = 0.1;  // 筛选命中但非关联：虚化
    else alpha = 0;                                            // 完全无关：隐藏
    ctx.globalAlpha = alpha;
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
    var lvl = graphNodeLevel(n);
    // 三态：hi 高亮 / dim 虚化 / hide 隐藏
    ctx.globalAlpha = lvl === "hi" ? 1 : lvl === "dim" ? 0.16 : 0;
    if (n.type === "herb") {
      // 名称标签：不再截断，按圆内可容纳宽度显示
      var label = n.id;
      ctx.font = (n.focus ? "bold 13px" : "13px") + " 'Microsoft YaHei', sans-serif";
      var labelW = ctx.measureText(label).width;
      // 圆半径：取基础值与"包住文字所需半径"的较大者，确保名称在圆内
      var baseR = n.focus ? 30 : 24;
      var r = Math.max(baseR, labelW / 2 + 9);
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
      if (n.focus) {
        graphPulse += 0.11;                       // 闪动计时（更快、更明显）
        var breathe = (Math.sin(graphPulse) + 1) / 2;   // 0~1 呼吸
        // 外层呼吸光环
        var haloR = r + 10 + breathe * 12;
        ctx.beginPath();
        ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(212,160,23," + (0.15 + (1 - breathe) * 0.55).toFixed(2) + ")";
        ctx.lineWidth = 2 + breathe * 1.5;
        ctx.stroke();
        // 内层快速闪动光环
        var haloR2 = r + 6 + Math.sin(graphPulse * 1.7) * 3;
        ctx.beginPath();
        ctx.arc(n.x, n.y, haloR2, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(255,221,120," + (0.45 + Math.abs(Math.sin(graphPulse * 1.7)) * 0.4).toFixed(2) + ")";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        // 节点本体径向外发光
        var glow = ctx.createRadialGradient(n.x, n.y, r * 0.4, n.x, n.y, r + 14);
        glow.addColorStop(0, "rgba(255,238,180,0.55)");
        glow.addColorStop(1, "rgba(212,160,23,0)");
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 14, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = n.focus ? GRAPH_FOCUS_COLOR : graphHerbColor(n);
      ctx.fill();
      ctx.lineWidth = n.focus ? 3.5 : 1.5;
      ctx.strokeStyle = n.focus ? "#8A5A00" : "rgba(0,0,0,0.35)";
      ctx.stroke();
      if (n.focus) {                              // 顶部"当前药材"标签牌
        ctx.fillStyle = "rgba(38,36,31,0.92)";
        var tw = 82, th = 22;
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(n.x - tw / 2, n.y - r - th - 14, tw, th, 6)
                      : ctx.rect(n.x - tw / 2, n.y - r - th - 14, tw, th);
        ctx.fill();
        ctx.beginPath();                          // 小三角指向节点
        ctx.moveTo(n.x - 5, n.y - r - 14);
        ctx.lineTo(n.x + 5, n.y - r - 14);
        ctx.lineTo(n.x, n.y - r - 8);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = "#FFFDF8";
        ctx.font = "bold 11px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText("🎯 当前药材", n.x, n.y - r - th - 14 + th / 2 + 1);
      }
      if (graphSelected && graphSelected.id === n.id) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = "#A93226";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.fillStyle = "#fff";
      ctx.font = (n.focus ? "bold 13px" : "13px") + " 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
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
    if (n.image) parts.push('<img class="herb-detail-img" src="' + esc(n.image) + '" alt="">');
    if (n.user_added) parts.push('<div class="muted">来源：用户增补（本草补遗库）</div>');
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

// 隐藏药材知识卡片（当前药材不再是聚焦对象时调用，如点击「全图浏览」/「返回上个图谱」）
function graphHideDetail() {
  var info = $("#graph-info");
  if (info) info.style.display = "none";
}

function graphCanvasToWorld(e) {
  var canvas = $("#graph-canvas");
  var rect = canvas.getBoundingClientRect();
  var W = 1040, H = 620;
  var mx = (e.clientX - rect.left) * (W / rect.width);
  var my = (e.clientY - rect.top) * (H / rect.height);
  return { x: (mx - graphPanX) / graphZoom, y: (my - graphPanY) / graphZoom };
}

function graphNodeRadius(n) {
  if (n.type !== "herb") return 9;
  var label = n.id;
  // 测量文字宽度需临时设置字体（与绘制一致）；自身获取 ctx，避免依赖 graphDraw 局部变量
  var gctx = $("#graph-canvas").getContext("2d");
  gctx.font = (n.focus ? "bold 13px" : "13px") + " 'Microsoft YaHei', sans-serif";
  var labelW = gctx.measureText(label).width;
  var baseR = n.focus ? 30 : 24;
  return Math.max(baseR, labelW / 2 + 9);
}

function graphHitTest(p) {
  for (var i = graphNodes.length - 1; i >= 0; i--) {
    var n = graphNodes[i];
    // 隐藏节点（被筛选裁掉）关闭点击接口，不可选中/拖拽
    if (graphNodeLevel(n) === "hide") continue;
    var r = graphNodeRadius(n);
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
    // 用户主动操作时解除视图锁定，尊重手动拖拽/平移
    graphViewLock = false;
    if (hit) {
      draggingNode = hit;
      hit.fixed = true;
      hit.x = p.x; hit.y = p.y;
      graphSimEnabled = true;   // 解冻力导向，拖拽时邻居可重排
      canvas.classList.add("dragging");
    } else {
      // 空白处拖拽 = 平移视图（仅平移，不再清除选中 / 触发其它功能）
      panning = true;
      lastX = e.clientX; lastY = e.clientY;
      canvas.classList.add("dragging");
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
      // 进入药材关联视图：显示「全体药材中与该药材有关的图谱」，与搜索该药材完全一致
      // （无视当前筛选条件，筛选只做暂存，不渲染）。同时屏蔽条件选择，点空白再恢复。
      // 仅在从筛选/全图进入时暂存当前筛选条件；若已在关联视图内继续点其他药材，
      // 则保留最初暂存的筛选（graphFiltersBak），避免被空筛选覆盖而丢失「最近一次」。
      if (!graphFilterLocked) {
        graphFiltersBak = JSON.parse(JSON.stringify(graphFilters));
      }
      graphFilters = { categories: [], property: [], toxicity: [], meridian: [] };
      graphFilterLocked = true;
      // 加载完整搜索图谱（关联最全），不保留筛选（保持干净关联视图）
      loadGraph(hit.id).then(function () {
        graphShowDetail(graphMeta[hit.id] || null);
        graphUpdateFilterTag();
        graphBuildLegend();          // 同步图例为禁用态
        // 药材关联子图先让力导向把初始堆叠/成环的节点展开散开（不冻结），
        // 待布局初步稳定后再重新铺满显示，避免基于堆叠布局 fit 导致节点溢出。
        graphSimEnabled = true;
        graphDraw();
        setTimeout(function () {
          if (graphFilterLocked) { graphFitAll(); graphDraw(); }
        }, 450);
        $("#graph-focus").value = hit.id;
      });
    }
    // 点击空白处不再触发任何功能；「复原视图比例 / 返回上个图谱」改为图谱上方悬浮按钮
  });

  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    var factor = e.deltaY < 0 ? 1.1 : 0.9;
    graphZoom = Math.min(3, Math.max(0.4, graphZoom * factor));
  }, { passive: false });

  // 图谱上方悬浮按钮：复原视图比例 / 返回上个图谱
  var btnResetView = $("#graph-reset-view");
  if (btnResetView) btnResetView.addEventListener("click", function () { graphResetView(); });
  var btnPrev = $("#graph-prev");
  if (btnPrev) btnPrev.addEventListener("click", function () { graphRestorePrev(); });
}

function graphUpdateFilterTag() {
  var tag = $("#graph-filter-tag");
  if (!tag) return;
  if (!graphHasFilter()) { tag.hidden = true; tag.innerHTML = ""; return; }
  var f = graphFilters;
  var labels = [];
  if (f.categories.length) labels.push("分类：" + f.categories.join("/"));
  if (f.property.length) labels.push("药性：" + f.property.join("/"));
  if (f.toxicity.length) labels.push("毒性：" + f.toxicity.join("/"));
  if (f.meridian.length) labels.push("归经：" + f.meridian.join("/"));
  var count = graphNodes.filter(function (n) {
    return n.type === "herb" && graphNodeMatch(n);
  }).length;
  var modeLabel = graphFilterMode === "or" ? "并集(或)" : "交集(且)";
  // 药材关联视图：条件已暂存并屏蔽，标签提示「查看关联中」，不显示清除按钮
  if (graphFilterLocked) {
    tag.hidden = false;
    tag.innerHTML = '正在查看药材关联（已暂存筛选条件，点击空白处恢复）';
    return;
  }
  tag.hidden = false;
  tag.innerHTML = '当前筛选（' + modeLabel + '）：<b>' + labels.join(graphFilterMode === "or" ? " 或 " : " 且 ") + "</b>（" + count + " 味）" +
    '<button type="button" class="graph-filter-clear" title="清除全部筛选">✕</button>';
  var clr = tag.querySelector(".graph-filter-clear");
  if (clr) clr.addEventListener("click", function () {
    graphEnsureAll();   // 清除筛选也回到全体药材
    graphFilters = { categories: [], property: [], toxicity: [], meridian: [] };
    graphSelected = null;
    graphUpdateHighlight(); graphBuildLegend(); graphDraw();
  });
}

function graphBuildLegend() {
  var leg = $("#graph-legend");
  var items = [];
  // —— 模式切换：交集 / 并集 ——
  items.push('<div class="leg-group leg-mode-group"><span class="leg-group-title">筛选模式</span><div class="leg-group-items">');
  items.push('<button type="button" class="item leg-mode' + (graphFilterMode === "and" ? " active" : "") +
    '" data-mode="and" title="交集：药材须同时满足所有已选条件">交集（且）</button>');
  items.push('<button type="button" class="item leg-mode' + (graphFilterMode === "or" ? " active" : "") +
    '" data-mode="or" title="并集：药材满足任一已选条件即可">并集（或）</button>');
  items.push('</div></div>');
  // —— 第一维：功效分类（可多选 OR 叠加）——
  items.push('<div class="leg-group"><span class="leg-group-title">功效分类（可多选叠加）</span><div class="leg-group-items">');
  Object.keys(GRAPH_CAT_COLORS).forEach(function (c) {
    var on = graphFilters.categories.indexOf(c) !== -1;
    items.push('<button type="button" class="item leg-cat' + (on ? " active" : "") +
      '" data-cat="' + esc(c) + '" title="叠加筛选「' + esc(c) + '」分类草药">' +
      '<span class="dot" style="background:' + GRAPH_CAT_COLORS[c] + '"></span>' + esc(c) + "</button>");
  });
  items.push('</div></div>');

  // —— 从数据动态提取其余维度可选值 ——
  function uniq(field, split) {
    var set = {};
    graphNodes.forEach(function (n) {
      if (n.type !== "herb") return;
      var v = n[field];
      if (!v) return;
      if (split) v.split(/[、,，\s]+/).forEach(function (x) { if (x) set[x] = 1; });
      else set[v] = 1;
    });
    return Object.keys(set);
  }
  // 药性（味 / 性 / 程度 三子组，子串组合选择，未选即不限制）
  items.push('<div class="leg-group"><span class="leg-group-title">药性（味/性/程度 可组合）</span>');
  function propSub(title, arr) {
    items.push('<div class="leg-sub"><span class="leg-sub-title">' + title + '</span><div class="leg-group-items">');
    arr.forEach(function (v) {
      var on = graphFilters.property.indexOf(v) !== -1;
      var cnt = graphLegendHitCount(function (n) { return (n.property || "").indexOf(v) !== -1; });
      items.push('<button type="button" class="item leg-prop' + (on ? " active" : "") +
        '" data-prop="' + esc(v) + '" title="药性含「' + esc(v) + '」（' + cnt + ' 味），未选即不限制">' +
        esc(v) + ' <em>' + cnt + '</em></button>');
    });
    items.push('</div></div>');
  }
  propSub("味", GRAPH_FLAVORS);
  propSub("性", GRAPH_NATURES);
  propSub("程度（如 苦+微=微苦）", GRAPH_DEGREES);
  items.push('</div>');
  // 毒性（按程度归类，整组切换）
  items.push('<div class="leg-group"><span class="leg-group-title">毒性</span><div class="leg-group-items">');
  GRAPH_TOX_GROUPS.forEach(function (g) {
    var on = g.vals.some(function (v) { return graphFilters.toxicity.indexOf(v) !== -1; });
    var cnt = graphLegendHitCount(function (n) { return g.vals.indexOf(n.toxicity || "无毒") !== -1; });
    items.push('<button type="button" class="item leg-tox' + (on ? " active" : "") +
      '" data-tox="' + esc(g.vals.join(",")) + '" title="' + esc(g.label) + '：' + esc(g.vals.join(" / ")) + '（' + cnt + ' 味）">' +
      esc(g.label) + ' <em>' + cnt + '</em></button>');
  });
  items.push('</div></div>');
  // 归经（按脏腑系统归类，整组切换）
  items.push('<div class="leg-group"><span class="leg-group-title">归经（按脏腑系统）</span><div class="leg-group-items">');
  GRAPH_MER_SYSTEMS.forEach(function (g) {
    var on = g.vals.some(function (v) { return graphFilters.meridian.indexOf(v) !== -1; });
    var cnt = graphLegendHitCount(function (n) {
      return graphMeridians(n.meridian).some(function (m) { return g.vals.indexOf(m) !== -1; });
    });
    items.push('<button type="button" class="item leg-mer' + (on ? " active" : "") +
      '" data-mer="' + esc(g.vals.join(",")) + '" title="' + esc(g.label) + '：' + esc(g.vals.join(" / ")) + '（' + cnt + ' 味）">' +
      esc(g.label) + ' <em>' + cnt + '</em></button>');
  });
  items.push('</div></div>');

  // —— 连线图例 ——
  items.push('<div class="leg-group"><span class="leg-group-title">连线</span><div class="leg-group-items">');
  Object.keys(GRAPH_REL_STYLE).forEach(function (r) {
    var st = GRAPH_REL_STYLE[r];
    items.push('<span class="item"><span class="line" style="border-top-color:' + st.color + '"></span>' + (GRAPH_REL_LABEL[r] || r) + "</span>");
  });
  items.push('</div></div>');

  leg.innerHTML = items.join("");
  // 药材关联视图中屏蔽条件选择：图例整体置灰禁用（点击处理器另有 graphFilterLocked 守卫）
  leg.classList.toggle("disabled", graphFilterLocked);
  graphUpdateFilterTag();

  // 点击分类：OR 叠加切换
  leg.querySelectorAll(".leg-cat").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (graphFilterLocked) return;   // 药材关联视图中屏蔽条件选择
      graphEnsureAll();   // 筛选始终基于全体药材
      var c = btn.dataset.cat;
      var i = graphFilters.categories.indexOf(c);
      if (i === -1) graphFilters.categories.push(c); else graphFilters.categories.splice(i, 1);
      graphSelected = null;
      graphUpdateHighlight(); graphBuildLegend(); graphFitToHighlight(); graphDraw();
      graphLogFilter();
    });
  });
  // 点击药性：OR 叠加切换
  leg.querySelectorAll(".leg-prop").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (graphFilterLocked) return;   // 药材关联视图中屏蔽条件选择
      graphEnsureAll();   // 筛选始终基于全体药材
      var p = btn.dataset.prop;
      var i = graphFilters.property.indexOf(p);
      if (i === -1) graphFilters.property.push(p); else graphFilters.property.splice(i, 1);
      graphSelected = null;
      graphUpdateHighlight(); graphBuildLegend(); graphFitToHighlight(); graphDraw();
      graphLogFilter();
    });
  });
  // 点击毒性：整组切换（如 低毒 = 小毒/微毒 一起加入或移除）
  leg.querySelectorAll(".leg-tox").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (graphFilterLocked) return;   // 药材关联视图中屏蔽条件选择
      graphEnsureAll();   // 筛选始终基于全体药材
      var vals = (btn.dataset.tox || "").split(",").filter(Boolean);
      var allIn = vals.every(function (v) { return graphFilters.toxicity.indexOf(v) !== -1; });
      if (allIn) {
        graphFilters.toxicity = graphFilters.toxicity.filter(function (v) { return vals.indexOf(v) === -1; });
      } else {
        vals.forEach(function (v) { if (graphFilters.toxicity.indexOf(v) === -1) graphFilters.toxicity.push(v); });
      }
      graphSelected = null;
      graphUpdateHighlight(); graphBuildLegend(); graphFitToHighlight(); graphDraw();
      graphLogFilter();
    });
  });
  // 点击归经：整组切换（如 肝胆 = 肝/胆 一起加入或移除）
  leg.querySelectorAll(".leg-mer").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (graphFilterLocked) return;   // 药材关联视图中屏蔽条件选择
      graphEnsureAll();   // 筛选始终基于全体药材
      var vals = (btn.dataset.mer || "").split(",").filter(Boolean);
      var allIn = vals.every(function (v) { return graphFilters.meridian.indexOf(v) !== -1; });
      if (allIn) {
        graphFilters.meridian = graphFilters.meridian.filter(function (v) { return vals.indexOf(v) === -1; });
      } else {
        vals.forEach(function (v) { if (graphFilters.meridian.indexOf(v) === -1) graphFilters.meridian.push(v); });
      }
      graphSelected = null;
      graphUpdateHighlight(); graphBuildLegend(); graphFitToHighlight(); graphDraw();
      graphLogFilter();
    });
  });
  // 点击模式切换：交集 / 并集
  leg.querySelectorAll(".leg-mode").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (graphFilterLocked) return;   // 药材关联视图中屏蔽条件选择
      graphEnsureAll();   // 筛选始终基于全体药材
      graphFilterMode = btn.dataset.mode;
      graphUpdateHighlight(); graphBuildLegend(); graphFitToHighlight(); graphDraw();
      graphLogFilter();
    });
  });
}

function graphLogFilter() {
  console.log("[graph] 筛选模式：" + (graphFilterMode === "or" ? "并集(或)" : "交集(且)"),
    "| 条件：", JSON.stringify(graphFilters),
    "| 命中高亮节点数：", graphHl.size, "| 总节点数：", graphNodes.length);
}

/* 将视图平移+缩放，使当前高亮（筛选/选中）的节点居中铺满 */
function graphFitToHighlight() {
  var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  // 以「高亮层级 hi」为准纳入包围盒（含命中药材及其关联中间节点），确保全部可见
  graphNodes.forEach(function (n) {
    if (graphNodeLevel(n) !== "hi") return;
    var nr = graphNodeRadius(n);   // 加上节点半径，避免大圆被裁边
    minX = Math.min(minX, n.x - nr); minY = Math.min(minY, n.y - nr);
    maxX = Math.max(maxX, n.x + nr); maxY = Math.max(maxY, n.y + nr);
  });
  if (minX === Infinity) return;
  var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  var bw = Math.max(maxX - minX, 60), bh = Math.max(maxY - minY, 60);
  var W = 1040, H = 620, pad = 100;
  var z = Math.min((W - pad * 2) / bw, (H - pad * 2) / bh, 2.2);
  z = Math.max(0.5, z);
  graphZoom = z;
  graphPanX = W / 2 - cx * z;
  graphPanY = H / 2 - cy * z;
  graphViewLock = false;   // 取消聚焦锁定，允许自由查看筛选结果
  graphSimEnabled = false; // 冻结力导向，使筛选/选中结果稳定停在窗口内不漂移
}

// 将整个图谱居中铺满（略小于画布，留边距），作为初始视图；可随时复原
function graphFitAll() {
  var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  graphNodes.forEach(function (n) {
    var nr = graphNodeRadius(n);
    minX = Math.min(minX, n.x - nr); minY = Math.min(minY, n.y - nr);
    maxX = Math.max(maxX, n.x + nr); maxY = Math.max(maxY, n.y + nr);
  });
  if (minX === Infinity) return;
  var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  var bw = Math.max(maxX - minX, 60), bh = Math.max(maxY - minY, 60);
  var W = 1040, H = 620;
  // 留边距：让图谱整体比显示界面略小一些
  var margin = Math.min(W, H) * 0.06;   // 约 6%
  var z = Math.min((W - margin * 2) / bw, (H - margin * 2) / bh, 2.2);
  z = Math.max(0.3, z);
  graphZoom = z;
  graphPanX = W / 2 - cx * z;
  graphPanY = H / 2 - cy * z;
  graphViewLock = false;
}

// 复原视图：将「当前图谱显示的所有药草」（高亮层级 hi，含筛选/选中后可见的节点）整体居中铺满，确保全部可见
function graphResetView() {
  var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  graphNodes.forEach(function (n) {
    if (graphNodeLevel(n) !== "hi") return;   // 仅纳入当前可见节点
    var nr = graphNodeRadius(n);
    minX = Math.min(minX, n.x - nr); minY = Math.min(minY, n.y - nr);
    maxX = Math.max(maxX, n.x + nr); maxY = Math.max(maxY, n.y + nr);
  });
  if (minX === Infinity) return;
  var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  var bw = Math.max(maxX - minX, 60), bh = Math.max(maxY - minY, 60);
  var W = 1040, H = 620, pad = 90;
  var z = Math.min((W - pad * 2) / bw, (H - pad * 2) / bh, 2.2);
  z = Math.max(0.5, z);
  graphZoom = z;
  graphPanX = W / 2 - cx * z;
  graphPanY = H / 2 - cy * z;
  graphViewLock = false;
  graphDraw();
}

function graphLoop() {
  // A 级 · 降级：用户偏好减少动效时，仅绘制一帧静态图，停止动画循环（防动晕 + 省电）
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) {
    graphDraw();
    return;
  }
  if (graphSimEnabled) graphSimulate();
  graphDraw();
  requestAnimationFrame(graphLoop);
}

// 返回「当前筛选条件下的全体药材图谱」（点空白处调用），不重新请求后端。
// 无论此前是否处于某药材的关联子图，点空白都回到全体药材，并按当前筛选条件
// 显示命中药材（有筛选则聚焦筛选结果，无筛选则铺满全图）。
function graphRestorePrev() {
  graphHideDetail();   // 离开聚焦药材，隐藏知识卡片
  if (!graphDataAll) return false;
  if (graphData === graphDataAll && !graphSelected) {
    // 已在全体图且无选中：仅复位视图即可，无需重建
    graphFitAll();
    graphDraw();
    return true;
  }
  // 还原点药材前暂存的筛选条件并解锁条件选择，回到「全体药材 + 当前筛选」图谱
  graphFilterLocked = false;
  graphData = graphDataAll;
  graphReset();                       // 注意：会清空 graphFilters，故需在下方恢复之后再保留
  if (graphFiltersBak) {
    graphFilters = graphFiltersBak;   // 在 graphReset 清空之后重新写入筛选条件
  }
  graphFiltersBak = null;
  graphBuild();
  graphBuildLegend();
  graphUpdateHighlight();
  graphUpdateFilterTag();
  // 有筛选：聚焦筛选结果，但保持力导向运行以把初始圆环堆叠的节点散开，
  // 不被 graphFitToHighlight 的冻结打断（否则成环形状会一直挤在一起）。
  if (graphHasFilter()) graphFitToHighlight(); else graphFitAll();
  graphSimEnabled = true;
  graphDraw();
  return true;
}

async function loadGraph(focus, opts) {
  opts = opts || {};
  var canvas = $("#graph-canvas");
  // 切换图谱时按需保留筛选条件（点药材查看完整关联但仍保留筛选，方便继续选其他药材）
  var savedFilters = null, savedMode = null;
  if (opts.keepFilters) {
    savedFilters = JSON.parse(JSON.stringify(graphFilters));
    savedMode = graphFilterMode;
  }
  try {
    var url = "/graph";
    if (focus) {
      if (Array.isArray(focus)) {
        url += "?" + focus.map(function (f) {
          return "focus=" + encodeURIComponent(f);
        }).join("&");
      } else {
        url += "?focus=" + encodeURIComponent(focus);
      }
    }
    var resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    graphData = await resp.json();
    if (graphData.categoryColors) {
      Object.assign(GRAPH_CAT_COLORS, graphData.categoryColors);
    }
    // 焦点为空 → 这是全体药材全图，记为筛选基准（每次筛选都基于全体药材）
    if (!focus) graphDataAll = graphData;
    graphReset();
    if (opts.keepFilters && savedFilters) {
      // 恢复筛选条件（图例按钮会据此重新高亮 active）
      graphFilters = savedFilters;
      graphFilterMode = savedMode;
    }
    graphBuild();
    graphBuildLegend();
    graphUpdateHighlight();
    graphUpdateFilterTag();
    // 初始视图：整个图谱居中铺满（略小于画布），可自由拖动/缩放
    graphFitAll();
    graphSimEnabled = true;   // 让初始图谱力导向展开稳定
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
  var raw = $("#graph-focus").value.trim();
  if (!raw) { alert("请输入药材名。"); return; }
  // 支持多药材：逗号/顿号/空格/分号分隔，传入数组以「多味聚焦」展示
  var names = raw.split(/[，,、;；\s]+/).map(function (s) { return s.trim(); })
    .filter(function (s) { return s; });
  // 若输入的药材名均不在知识库中，提示加入「本草补遗库」
  var unknown = names.filter(function (n) { return !allHerbNames.has(n); });
  if (unknown.length) { showGraphNotFound(unknown); return; }
  // 搜索新药材即从药材关联视图/筛选暂存中离开，解除条件屏蔽
  graphFilterLocked = false; graphFiltersBak = null;
  graphHideDetail();   // 切换聚焦药材，隐藏旧知识卡片
  loadGraph(names.length === 1 ? names[0] : names);
});
$("[data-graph-all]").addEventListener("click", function () {
  $("#graph-focus").value = "";
  graphFilterLocked = false; graphFiltersBak = null;
  graphHideDetail();   // 回到全图，无当前药材，隐藏知识卡片
  loadGraph("");
});
$("#graph-focus").addEventListener("keydown", function (e) {
  if (e.key === "Enter") $("[data-graph-load]").click();
});

/* 药材名补全（datalist）+ 全部可检索药材名（含用户增补） */
var allHerbNames = new Set();
async function loadHerbNames() {
  try {
    var resp = await fetch("/herbs");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();
    var list = data.herbs || data.names || [];
    allHerbNames = new Set(list);
    var dl = $("#graph-herbs");
    dl.innerHTML = list.map(function (n) {
      return '<option value="' + esc(n) + '"></option>';
    }).join("");
  } catch (err) {
    console.error("药材名加载失败", err);
  }
}

loadHerbNames();

/* ====================== 用户增补药材库（本草补遗库） ====================== */
var pendingNotFoundNames = "";

function showGraphNotFound(unknown) {
  pendingNotFoundNames = unknown.join("、");
  var box = recEl("graph-notfound");
  if (!box) return;
  recEl("graph-notfound-text").innerHTML = "知识库中未找到：<b>" + esc(pendingNotFoundNames) +
    "</b>。是否将其加入「本草补遗库」后纳入检索与图谱？";
  box.hidden = false;
}
function hideGraphNotFound() { var b = recEl("graph-notfound"); if (b) b.hidden = true; }

var currentEditName = null;   // 编辑时的原药名（null=新增）
var currentEditImage = null;  // 编辑时的现有图片 URL

function openUserHerbForm(herb, prefillName) {
  currentEditName = herb ? herb.name : null;
  currentEditImage = herb ? (herb.image || null) : null;
  recEl("userHerbFormTitle").textContent = herb ? ("编辑药材：" + herb.name) : "添加药材（本草补遗库）";
  recEl("uhName").value = herb ? (herb.name || "") : (prefillName || "");
  recEl("uhProperty").value = herb ? (herb.property || "") : "";
  recEl("uhMeridian").value = herb ? (herb.meridian || "") : "";
  recEl("uhFunction").value = herb ? (herb["function"] || "") : "";
  recEl("uhAliases").value = (herb && herb.aliases) ? herb.aliases.join("、") : "";
  recEl("uhIndications").value = herb ? (herb.indications || "") : "";
  recEl("uhCautions").value = herb ? (herb.cautions || "") : "";
  recEl("uhPaired").value = herb ? (herb.paired_herb || "") : "";
  recEl("uhImage").value = "";
  var prev = recEl("uhImagePreview");
  if (herb && herb.image) { prev.src = herb.image; prev.hidden = false; }
  else { prev.hidden = true; prev.removeAttribute("src"); }
  recEl("userHerbForm").hidden = false;
}
function closeUserHerbForm() { recEl("userHerbForm").hidden = true; }

function saveUserHerb() {
  var name = recEl("uhName").value.trim();
  if (!name) { toast("请填写药名"); return; }
  var record = {
    name: name,
    property: recEl("uhProperty").value.trim(),
    meridian: recEl("uhMeridian").value.trim(),
    "function": recEl("uhFunction").value.trim(),
    aliases: recEl("uhAliases").value.trim(),
    indications: recEl("uhIndications").value.trim(),
    cautions: recEl("uhCautions").value.trim(),
    paired_herb: recEl("uhPaired").value.trim(),
    image: null
  };
  var fileInput = recEl("uhImage");
  var file = fileInput.files && fileInput.files[0];
  function doSave(imageDataUrl) {
    record.image = imageDataUrl;
    var editing = currentEditName;
    var url = editing ? ("/api/user_herbs/" + encodeURIComponent(editing)) : "/api/user_herbs";
    var method = editing ? "PUT" : "POST";
    fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record)
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || ("HTTP " + r.status)); },
        function () { throw new Error("HTTP " + r.status); });
      return r.json();
    }).then(function () {
      toast(editing ? "已更新药材" : "已加入本草补遗库");
      closeUserHerbForm();
      hideGraphNotFound();
      loadHerbNames().then(function () {
        loadGraph([name], { keepFilters: false });
        if (!recEl("userHerbLib").hidden) renderUserHerbLib();
      });
    }).catch(function (err) { toast("保存失败：" + err.message); });
  }
  if (file) {
    var reader = new FileReader();
    reader.onload = function () { doSave(reader.result); };
    reader.onerror = function () { doSave(null); };
    reader.readAsDataURL(file);
  } else {
    doSave(null);  // 编辑且未换图时由后端保留原图
  }
}

function openUserHerbLib() { recEl("userHerbLib").hidden = false; renderUserHerbLib(); }
function closeUserHerbLib() { recEl("userHerbLib").hidden = true; }

function renderUserHerbLib() {
  var list = recEl("userHerbLibList");
  if (!list) return;
  list.innerHTML = '<div class="user-herb-loading">加载中…</div>';
  fetch("/api/user_herbs").then(function (r) { return r.json(); }).then(function (d) {
    var herbs = d.herbs || [];
    if (!herbs.length) {
      list.innerHTML = '<div class="user-herb-empty">暂无增补药材。点击「+ 新增药材」开始添加。</div>';
      return;
    }
    list.innerHTML = herbs.map(function (h) {
      var img = h.image
        ? '<img class="user-herb-thumb" src="' + esc(h.image) + '" alt="">'
        : '<div class="user-herb-thumb user-herb-thumb-empty">无图</div>';
      return '<div class="user-herb-item" data-name="' + esc(h.name) + '">' + img +
        '<div class="user-herb-info"><div class="user-herb-name">' + esc(h.name) + '</div>' +
        '<div class="user-herb-sub">' + esc(h.property || "—") + " · " + esc(h.meridian || "—") + '</div></div>' +
        '<div class="user-herb-ops">' +
        '<button type="button" class="fav-mini-btn" data-uh-edit="' + esc(h.name) + '">编辑</button>' +
        '<button type="button" class="fav-mini-btn user-herb-del" data-uh-del="' + esc(h.name) + '">删除</button>' +
        '</div></div>';
    }).join("");
  }).catch(function () { list.innerHTML = '<div class="user-herb-empty">加载失败。</div>'; });
}

function editUserHerb(name) {
  closeUserHerbLib();
  fetch("/api/user_herbs").then(function (r) { return r.json(); }).then(function (d) {
    var h = (d.herbs || []).find(function (x) { return x.name === name; });
    if (!h) { toast("未找到该药材"); return; }
    openUserHerbForm(h);
  });
}

function deleteUserHerb(name) {
  if (!confirm("确定删除「" + name + "」？删除后将同时从检索与图谱中移除。")) return;
  fetch("/api/user_herbs/" + encodeURIComponent(name), { method: "DELETE" }).then(function (r) {
    if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || ("HTTP " + r.status)); },
      function () { throw new Error("HTTP " + r.status); });
    return r.json();
  }).then(function () {
    toast("已删除");
    renderUserHerbLib();
    loadHerbNames().then(function () { loadGraph("", {}); });
  }).catch(function (err) { toast("删除失败：" + err.message); });
}

function initUserHerb() {
  var libBtn = recEl("btn-user-herb-lib");
  if (libBtn) libBtn.addEventListener("click", openUserHerbLib);
  var saveBtn = recEl("userHerbSave");
  if (saveBtn) saveBtn.addEventListener("click", saveUserHerb);
  document.querySelectorAll("[data-user-herb-form-close]").forEach(function (el) {
    el.addEventListener("click", closeUserHerbForm);
  });
  document.querySelectorAll("[data-user-herb-lib-close]").forEach(function (el) {
    el.addEventListener("click", closeUserHerbLib);
  });
  var addNew = recEl("userHerbAddNew");
  if (addNew) addNew.addEventListener("click", function () { closeUserHerbLib(); openUserHerbForm(null); });
  var imgInput = recEl("uhImage");
  if (imgInput) imgInput.addEventListener("change", function () {
    var f = imgInput.files && imgInput.files[0];
    var prev = recEl("uhImagePreview");
    if (f) {
      var rd = new FileReader();
      rd.onload = function () { prev.src = rd.result; prev.hidden = false; };
      rd.readAsDataURL(f);
    } else { prev.hidden = true; }
  });
  var nfAdd = recEl("graph-notfound-add");
  if (nfAdd) nfAdd.addEventListener("click", function () { openUserHerbForm(null, pendingNotFoundNames); });
  var nfClose = recEl("graph-notfound-close");
  if (nfClose) nfClose.addEventListener("click", hideGraphNotFound);
  var list = recEl("userHerbLibList");
  if (list) list.addEventListener("click", function (e) {
    var t = e.target;
    var en = t.getAttribute && t.getAttribute("data-uh-edit");
    var dn = t.getAttribute && t.getAttribute("data-uh-del");
    if (en) editUserHerb(en);
    else if (dn) deleteUserHerb(dn);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeUserHerbForm(); closeUserHerbLib(); }
  });
}

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
