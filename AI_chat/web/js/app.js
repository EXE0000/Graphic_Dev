/* ============================================================
   app.js —— STARTY 外壳的「后端绑定层」（完整功能版）。
   红线：不修改外壳的 HTML/CSS/类名/颜色/尺寸；所有新增控件都用
   createElement 注入，样式只走 app.css 里新增的类。
   外壳内联脚本已负责 Tab 切换 / 开关圆点 / 上拉菜单 / 输入框撑高，
   本文件在其后「读状态 + 绑事件 + 调 /api/*」，把旧网页端的全部功能
   （搜索 / 停止 / 远程流恢复 / 会话管理 / 导出 / 删除 / 清空 / 图片保存 /
   引用 / 数学公式 / 滚动跟随 / 状态栏）都接回来。
   配置归属桌面端：开关 / 推理等级走 /api/setting，模型列表由桌面端提供、
   网页端选一个走 /api/model，两端勾选状态互相同步。
   ============================================================ */
(function () {
  "use strict";

  // ------------------------------------------------------------ 基础工具
  var token = new URLSearchParams(location.search).get("t") || "";
  var currentConv = "";
  function api(p) {
    var url = p + (p.indexOf("?") >= 0 ? "&" : "?") + "t=" + encodeURIComponent(token);
    if (currentConv && p.indexOf("conv=") < 0) url += "&conv=" + encodeURIComponent(currentConv);
    return url;
  }
  function getJSON(p) { return fetch(api(p)).then(function (r) { return r.json(); }); }
  function postJSON(p, body) {
    return fetch(api(p), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }
  function postRaw(p, body) {
    return fetch(api(p), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // 来源 URL 属于不可信内容，只放行 http(s)，避免 javascript:/data: XSS
  function safeUrl(u) { u = String(u || "").trim(); return /^https?:\/\//i.test(u) ? u : "#"; }

  // ------------------------------------------------------------ 全局状态
  var conversations = [];
  var pendingItems = [];
  var busy = false;             // 本页正在接收 SSE
  var remoteStreaming = false;  // 刷新后服务端仍在生成
  var serverBusy = false;       // 任意会话正在生成（别的会话里禁发）
  var modelReady = false;
  var currentModel = "";
  var currentController = null;
  var stick = true;             // 是否跟随最新内容
  var managing = false;         // 消息批量管理
  var convManaging = false;     // 会话批量管理
  var convSelected = {};
  var searchTimer = null, lastSearch = "";
  var streamPoll = null, busyTimer = null;
  var stateIndex = -1;

  var REASONING_TEXT = { off: "关闭", low: "低", medium: "中", high: "高" };
  var LEVEL_TO_REASON = { "低": "low", "中": "medium", "高": "high", "MAX": "high" };
  var REASON_TO_LEVEL = { low: "低", medium: "中", high: "高" };
  var EXPORT_FORMATS = [["md", "MD"], ["docx", "Word"], ["pdf", "PDF"], ["xlsx", "Excel"]];

  // ------------------------------------------------------------ DOM 引用（外壳既有）
  var stage = document.getElementById("chatStage");
  var input = document.getElementById("composerInput");
  var sendBtn = document.getElementById("sendBtn");
  var newChatBtn = document.getElementById("newChatBtn");
  var addFileBtn = document.getElementById("addFileBtn");
  var profileBtn = document.getElementById("profileBtn");
  var panelHistory = document.getElementById("panel-history");
  var panelKnowledge = document.getElementById("panel-knowledge");
  var mainEl = document.querySelector(".main");
  var composerDock = document.querySelector(".composer-dock");
  var fileStrip = document.querySelector(".file-strip");
  var greeting = stage.querySelector(".greeting");
  var histEmpty = panelHistory.querySelector(".empty-state");
  var SEND_ICON = sendBtn.innerHTML;   // 外壳原始「↑」图标，停止态会临时换成「■」

  function switchTool(label) {
    var tools = document.querySelectorAll(".tool--switch");
    for (var i = 0; i < tools.length; i++) {
      var lab = tools[i].querySelector(".tool__label");
      if (lab && lab.textContent.trim() === label) return tools[i];
    }
    return null;
  }
  var thinkTool = switchTool("思考模式");
  var searchTool = switchTool("联网模式");
  var levelTool = document.querySelector(".tool--level");
  var modelTool = document.querySelector(".tool--model");
  function isOn(t) { return t && t.classList.contains("is-on"); }
  function setOn(t, on) {
    if (!t) return;
    t.classList.toggle("is-on", on);
    t.querySelector(".tool__trigger").setAttribute("aria-checked", String(on));
  }
  function levelText() { return levelTool.querySelector(".tool__value-text").textContent.trim(); }

  // 注入控件的引用（在 build* 里赋值）
  var statusEl, topbarNormal, topbarManage, topbarFmt, topbarDeleteSel;
  var histSearch, histSearchClear, histSearchMeta, histList, histManageNormal, histManageOn, histDeleteSel;
  var jumpLatest;

  function toast(msg) {
    var t = el("div", "toast", msg);
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }
  function setStatus(msg) { if (statusEl) statusEl.textContent = msg; islandSet(msg, busy); }
  function setTitle(text) { document.title = (text ? text + " · " : "") + "STARTY"; }

  // ------------------------------------------------------------ 渲染：消毒 / 高亮 / 数学 / 图片 / 引用
  var BLOCKED = ["script", "iframe", "object", "embed", "link", "meta", "style",
                 "base", "form", "input", "button", "textarea", "select", "svg"];
  function sanitizeHtml(html) {
    if (!html) return "";
    var doc;
    try { doc = new DOMParser().parseFromString(html, "text/html"); }
    catch (e) { return escapeHtml(html); }
    BLOCKED.forEach(function (tag) {
      Array.prototype.forEach.call(doc.querySelectorAll(tag), function (n) {
        n.parentNode && n.parentNode.removeChild(n);
      });
    });
    Array.prototype.forEach.call(doc.querySelectorAll("*"), function (n) {
      for (var i = n.attributes.length - 1; i >= 0; i--) {
        var a = n.attributes[i], name = a.name.toLowerCase();
        var v = (a.value || "").replace(/\s+/g, "").toLowerCase();
        var bad = name.indexOf("on") === 0 ||
          ((name === "href" || name === "src" || name === "action" ||
            name === "xlink:href" || name === "formaction") &&
           (v.indexOf("javascript:") === 0 || v.indexOf("data:text/html") === 0 ||
            v.indexOf("vbscript:") === 0));
        if (bad) n.removeAttribute(a.name);
      }
      if (n.tagName === "A" && n.getAttribute("href")) {
        n.setAttribute("target", "_blank");
        n.setAttribute("rel", "noopener noreferrer");
      }
    });
    return doc.body.innerHTML;
  }
  function renderMarkdown(text) {
    var html = window.marked ? marked.parse(text || "") : escapeHtml(text || "");
    return sanitizeHtml(html);
  }
  function typeset(node) {
    if (!window.renderMathInElement) return;
    try {
      renderMathInElement(node, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\[", right: "\\]", display: true },
          { left: "\\(", right: "\\)", display: false }
        ],
        throwOnError: false,
        ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
      });
    } catch (e) { /* 公式出错不影响正文 */ }
  }
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () { return legacyCopy(text); });
    }
    return legacyCopy(text);
  }
  function legacyCopy(text) {
    return new Promise(function (resolve, reject) {
      var ta = el("textarea"); ta.value = text;
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); }
      catch (e) { reject(e); }
      document.body.removeChild(ta);
    });
  }
  // 代码块：高亮 + 语言标签 + 复制（消毒之后 createElement 构建）
  function decorateCodeBlocks(root) {
    Array.prototype.forEach.call(root.querySelectorAll("pre > code"), function (code) {
      var pre = code.parentNode;
      if (!pre || !pre.parentNode || pre.parentNode.classList.contains("code-block")) return;
      var m = /language-([\w+-]+)/.exec(code.className || "");
      if (window.hljs) { try { hljs.highlightElement(code); } catch (e) {} }
      var wrap = el("div", "code-block");
      pre.parentNode.insertBefore(wrap, pre);
      var bar = el("div", "code-block__bar");
      bar.appendChild(el("span", "code-block__lang", m ? m[1] : "text"));
      var copy = el("button", "code-block__copy", "复制"); copy.type = "button";
      copy.addEventListener("click", function () {
        copyText(code.textContent).then(function () {
          copy.textContent = "已复制"; copy.classList.add("is-copied");
          setTimeout(function () { copy.textContent = "复制"; copy.classList.remove("is-copied"); }, 1500);
        }).catch(function () {
          copy.textContent = "复制失败";
          setTimeout(function () { copy.textContent = "复制"; }, 1500);
        });
      });
      bar.appendChild(copy);
      wrap.appendChild(bar); wrap.appendChild(pre);
    });
  }
  // 正文图片加「保存图片」按钮
  function decorateImages(root) {
    Array.prototype.forEach.call(root.querySelectorAll("img:not(.img-decorated)"), function (img) {
      img.classList.add("img-decorated");
      var wrap = el("span", "img-wrap");
      img.parentNode.insertBefore(wrap, img);
      wrap.appendChild(img);
      var btn = el("button", "img-save", "保存图片"); btn.type = "button"; btn.title = "另存为…";
      btn.addEventListener("click", function () { saveImage(img.src); });
      wrap.appendChild(btn);
    });
  }
  function saveImage(src) {
    fetch(src).then(function (r) { return r.blob(); }).then(function (blob) {
      var name = src.split("?")[0].split("/").pop() || "image.png";
      if (name.indexOf(".") < 0) name += ".png";
      var link = el("a"); link.href = URL.createObjectURL(blob); link.download = name;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
    }).catch(function () { toast("保存失败：图片可能已被删除"); });
  }
  // 把正文 [1][2] 变成可点击来源链接（跳过代码块和已有链接）
  function linkCitations(root, sources) {
    if (!root || !sources || !sources.length) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || node.nodeValue.indexOf("[") < 0) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        while (p && p !== root) {
          if (p.tagName === "A" || p.tagName === "CODE" || p.tagName === "PRE") return NodeFilter.FILTER_REJECT;
          p = p.parentNode;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var safe = escapeHtml(node.nodeValue);
      var html = safe.replace(/\[(\d{1,2})\]/g, function (match, num) {
        var s = sources[parseInt(num, 10) - 1];
        if (!s || !s.url) return match;
        var url = safeUrl(s.url);
        if (url === "#") return match;
        var title = escapeHtml(s.title || s.url || "");
        return '<a class="cite" href="' + escapeHtml(url) + '" target="_blank"'
             + ' rel="noopener noreferrer" title="' + title + '">' + match + "</a>";
      });
      if (html === safe) return;
      var span = el("span"); span.innerHTML = html;
      node.parentNode.replaceChild(span, node);
    });
  }
  function paintBody(node, markdown, sources) {
    if (!node) return;
    node.innerHTML = renderMarkdown(markdown || "");
    typeset(node);
    decorateImages(node);
    linkCitations(node, sources);
    decorateCodeBlocks(node);
  }

  // ------------------------------------------------------------ 消息流
  var msgList = null;
  var startersEl = null;
  // 空会话的引导卡片：点一下把提示词填进输入框（官方首页的 starter prompts）
  var STARTERS = [
    "用一句话解释量子纠缠",
    "帮我写一个 Python 快速排序",
    "把「你好，世界」翻译成英文",
    "给我一个周末两日游的计划"
  ];
  function buildStarters() {
    var wrap = el("div", "starters");
    STARTERS.forEach(function (s) {
      var b = el("button", "starter", s); b.type = "button";
      b.addEventListener("click", function () {
        input.value = s;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      });
      wrap.appendChild(b);
    });
    return wrap;
  }
  function ensureList() {
    if (greeting && greeting.parentNode) greeting.remove();
    if (startersEl && startersEl.parentNode) startersEl.remove();
    startersEl = null;
    if (!msgList || !msgList.parentNode) {
      msgList = el("div", "msg-list ui-column");
      stage.appendChild(msgList);
    }
    stage.classList.add("has-msgs");
    return msgList;
  }
  function showGreeting() {
    stage.innerHTML = ""; msgList = null; startersEl = null;
    stage.classList.remove("has-msgs", "managing");
    if (greeting) {
      stage.appendChild(greeting);
      if (!greeting.querySelector(".greeting-persona"))
        greeting.appendChild(el("p", "greeting-persona"));
    }
    applyPersona();
    startersEl = buildStarters();
    stage.appendChild(startersEl);
  }
  function nearBottom() {
    return stage.scrollHeight - stage.scrollTop - stage.clientHeight < 40;
  }
  function scrollBottom(force) {
    if (force) stick = true;
    stage.scrollTop = stage.scrollHeight;
    if (jumpLatest) jumpLatest.hidden = true;
  }
  function updateJump() {
    if (!stage.classList.contains("has-msgs")) { if (jumpLatest) jumpLatest.hidden = true; return; }
    stick = nearBottom();
    if (jumpLatest) jumpLatest.hidden = stick;
  }
  // 只在指定容器内滚动，绝不带动根文档：zoom 下 scrollIntoView 会误滚 root，
  // 把整个 .app 顶上去、页面底部留下一条空档
  function scrollWithin(container, node, offset) {
    var cr = container.getBoundingClientRect(), nr = node.getBoundingClientRect();
    if (nr.top >= cr.top && nr.bottom <= cr.bottom) return;
    container.scrollTop += (nr.top - cr.top) - (offset || 0);
  }
  function keepRootStill() {
    if (window.scrollY || document.documentElement.scrollTop) window.scrollTo(0, 0);
  }
  // 构建一条消息：复选框 + 气泡列（官方布局不再放头像/角色名，删除收进悬停操作条）
  function addMsg(role, index) {
    var list = ensureList();
    var msg = el("div", "msg " + (role === "user" ? "msg--user" : "msg--ai"));
    if (index != null) msg.dataset.index = index;
    var check = el("input", "msg-check"); check.type = "checkbox";
    check.addEventListener("change", refreshSelectedCount);
    msg.appendChild(check);
    var col = el("div", "msg__col");
    if (role !== "user") {
      var pn = personaNode();
      if (pn) col.appendChild(pn);
    }
    var body = el("div", "msg__body");
    col.appendChild(body);
    msg.appendChild(col);
    list.appendChild(msg);
    if (stick) scrollBottom();
    return { msg: msg, col: col, body: body };
  }
  // 附件 chip：点击让客户端调用系统默认应用预览（浏览器自己碰不到本地文件）。
  // ref 只是定位符：待发送用 {pending: 下标}，历史消息用 {src, i} + 所在消息的 data-index。
  function attachChip(text, openable, ref) {
    var chip = el("span", "file-chip", text);
    if (!openable) return chip;              // 旧记录没留副本，点了也打不开
    chip.classList.add("file-chip--open");
    chip.title = "点击用系统默认应用打开";
    chip.addEventListener("click", function () { openAttachment(chip, ref); });
    return chip;
  }
  function openAttachment(chip, ref) {
    var body = {};
    if (ref.pending != null) {
      body.pending = ref.pending;
    } else {
      var msg = chip.closest(".msg");
      var raw = msg ? msg.getAttribute("data-index") : null;
      if (raw == null || raw === "") { toast("这条消息还在跟服务端同步，稍后再点"); return; }
      body.index = Number(raw); body.src = ref.src; body.i = ref.i;
    }
    postRaw("/api/open", body).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { toast(t || "打开附件失败"); });
      return r.json().then(function (d) {
        toast("已用系统默认应用打开 " + (d.name || "附件"));
      });
    }).catch(function () { toast("客户端没有响应，确认它还开着再试"); });
  }
  function appendFiles(body, items) {
    if (!items || !items.length) return;
    var box = el("div", "msg__files");
    items.forEach(function (a) {
      box.appendChild(attachChip((a.kind === "image" ? "🖼 " : "📄 ") + (a.name || ""),
                                 a.openable !== false,
                                 { src: a.src || "files",
                                   i: a.i != null ? a.i : a.id }));
    });
    body.parentNode.appendChild(box);
  }
  function makeThinking() {
    var t = el("span", "thinking");
    t.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
                + '<path d="M12 3a9 9 0 1 0 9 9"/></svg><span>正在思考…</span>';
    return t;
  }
  function addThinkDetails(body, text, open, before) {
    var d = el("details", "msg__think"); d.open = (open !== false);
    var sum = el("summary");
    sum.innerHTML = '<span class="think-ico">' + ICONS.think + '</span>'
                  + '<span class="think-label">' + (open !== false ? "正在思考…" : "已深度思考") + '</span>'
                  + '<span class="think-chev">' + ICONS.chevR + '</span>';
    d.appendChild(sum);
    var b = el("div", "msg__think-body");
    b.innerHTML = renderMarkdown(text || ""); typeset(b);
    d.appendChild(b);
    // 思考块必须插在正文之前（官方客户端顺序），不能 append 到正文后面
    if (before && before.parentNode === body) body.insertBefore(d, before);
    else body.appendChild(d);
    return { details: d, body: b };
  }
  // 回答收尾：折叠思考块并标注耗时（DeepSeek/ChatGPT 同款「已深度思考 · 用时 Ns」）
  function finishThink(think, seconds) {
    if (!think) return;
    think.details.open = false;
    think.details.classList.add("is-done");
    var lab = think.details.querySelector(".think-label");
    if (lab) lab.textContent = "已深度思考" + (seconds ? " · 用时 " + seconds + " 秒" : "");
  }
  // 来源：可折叠 + 日期徽标 + 外链
  function renderSources(col, sources) {
    if (!sources || !sources.length) return null;
    var d = el("details", "msg__sources");
    d.appendChild(el("summary", null, "联网搜索 · " + sources.length + " 个来源"));
    var list = el("ol");
    sources.forEach(function (s) {
      var li = el("li");
      if (s.date) li.appendChild(el("span", "src-date", s.date));
      var a = el("a", null, s.title || s.url);
      a.href = safeUrl(s.url); a.target = "_blank"; a.rel = "noopener noreferrer";
      li.appendChild(a); list.appendChild(li);
    });
    d.appendChild(list); col.appendChild(d);
    return d;
  }
  // token 用量：低调一行灰字，输入/输出明细放 title 悬停可见
  function renderUsage(col, usage) {
    if (!usage || !usage.total_tokens) return null;
    var total = Number(usage.total_tokens) || 0;
    var pt = Number(usage.prompt_tokens) || 0;
    var ct = Number(usage.completion_tokens) || 0;
    var d = el("div", "msg-usage", "本次消耗 " + total.toLocaleString() + " tokens");
    d.title = "输入 " + pt.toLocaleString() + " · 输出 " + ct.toLocaleString();
    col.appendChild(d);
    return d;
  }
  // 官方风格的线性图标：操作条默认隐藏，悬停消息才浮现
  var ICONS = {
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    regen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/></svg>',
    export: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    del: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    think: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/></svg>',
    chevL: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
    chevR: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'
  };
  function iconBtn(title, svg) {
    var b = el("button", "msg-tool"); b.type = "button";
    b.title = title; b.setAttribute("aria-label", title); b.innerHTML = svg;
    return b;
  }
  function closeMsgMenus() {
    Array.prototype.forEach.call(document.querySelectorAll(".msg-menu.is-open"), function (m) { m.classList.remove("is-open"); });
  }
  // 导出收进单个图标 + 弹出菜单，避免四个文字按钮占满操作条
  function exportMenu(index) {
    var wrap = el("span", "msg-export");
    var btn = iconBtn("导出这条回答", ICONS.export);
    var menu = el("div", "msg-menu");
    EXPORT_FORMATS.forEach(function (pair) {
      var o = el("button", null, pair[1]); o.type = "button";
      o.addEventListener("click", function () {
        closeMsgMenus(); o.disabled = true;
        postRaw("/api/export", { index: index, fmt: pair[0] })
          .then(function (resp) { return downloadResponse(resp, "answer." + pair[0]); })
          .catch(function (e) { toast("导出失败：" + e); })
          .then(function () { o.disabled = false; });
      });
      menu.appendChild(o);
    });
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = menu.classList.contains("is-open");
      closeMsgMenus(); if (!open) menu.classList.add("is-open");
    });
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    wrap.appendChild(btn); wrap.appendChild(menu);
    return wrap;
  }
  // 单条回答的操作条：复制 / 重新生成（仅末条）/ 导出菜单 / 删除
  function addTurnTools(col, index, content, isLast) {
    var bar = el("div", "msg-tools");
    var copy = iconBtn("复制这条回答", ICONS.copy);
    copy.addEventListener("click", function () {
      copyText(content || "").then(function () { toast("已复制"); }).catch(function () { toast("复制失败"); });
    });
    bar.appendChild(copy);
    if (isLast) {
      var regen = iconBtn("重新生成", ICONS.regen);
      regen.title = "丢弃这条回答，用同一个问题再答一次";
      regen.addEventListener("click", function () { regenerate(index); });
      bar.appendChild(regen);
    }
    bar.appendChild(exportMenu(index));
    var del = iconBtn("删除这条对话", ICONS.del);
    del.addEventListener("click", function () { deleteTurns([index]); });
    bar.appendChild(del);
    col.appendChild(bar);
  }
  // 用户消息的操作条：编辑重发 / 删除
  function addUserEdit(col, msg, index, content) {
    var bar = el("div", "msg-tools");
    var edit = iconBtn("编辑这条消息并重新发送", ICONS.edit);
    edit.addEventListener("click", function () { startEdit(msg, index, content); });
    bar.appendChild(edit);
    var del = iconBtn("删除这条对话", ICONS.del);
    del.addEventListener("click", function () { deleteTurns([index]); });
    bar.appendChild(del);
    col.appendChild(bar);
  }
  function downloadResponse(resp, fallbackName) {
    if (!resp.ok) return resp.text().then(function (t) { toast("导出失败：" + t); });
    var dispo = resp.headers.get("Content-Disposition") || "";
    var m = /filename\*=UTF-8''([^;]+)/.exec(dispo);
    var name = m ? decodeURIComponent(m[1]) : fallbackName;
    return resp.blob().then(function (blob) {
      var link = el("a"); link.href = URL.createObjectURL(blob); link.download = name;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
    });
  }

  // ------------------------------------------------------------ 发送按钮状态机
  function updateSendBtn() {
    islandSet(busy || remoteStreaming ? "正在生成…" : (statusEl ? statusEl.textContent : ""),
              busy || remoteStreaming);
    sendBtn.classList.remove("is-stopping", "is-blocked");
    if (busy || remoteStreaming) {
      sendBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">'
        + '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/></svg>';
      sendBtn.disabled = false; sendBtn.classList.add("is-stopping");
      sendBtn.title = "停止生成"; sendBtn.setAttribute("aria-label", "停止生成");
      return;
    }
    sendBtn.innerHTML = SEND_ICON;
    if (serverBusy) {
      sendBtn.disabled = true; sendBtn.classList.add("is-blocked");
      sendBtn.title = "另一个对话正在生成，完成后再发送"; sendBtn.setAttribute("aria-label", "发送");
      return;
    }
    if (!modelReady) {
      sendBtn.disabled = true; sendBtn.classList.add("is-blocked");
      sendBtn.title = "请先选择模型"; sendBtn.setAttribute("aria-label", "发送");
      return;
    }
    sendBtn.disabled = false; sendBtn.title = "发送"; sendBtn.setAttribute("aria-label", "发送");
  }

  // ------------------------------------------------------------ SSE 发送
  function handleEvent(block, state) {
    var ev = "message", data = "";
    block.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
    });
    if (!data) return;
    var p; try { p = JSON.parse(data); } catch (e) { return; }
    if (ev === "chunk") { state.text += p.text || ""; state.dirty = true; }
    else if (ev === "think") { state.think += p.text || ""; state.thinkDirty = true; }
    else if (ev === "note") { setStatus(p.text || ""); }
    else if (ev === "sources") { state.sources = p.items || []; }
    else if (ev === "usage") { state.usage = p.usage || {}; }
    else if (ev === "image") { state.text += "\n\n" + (p.markdown || "") + "\n\n"; state.dirty = true; }
    else if (ev === "error") { state.text += "\n\n**出错了：** " + (p.text || ""); state.dirty = true; }
    else if (ev === "done") { state.finished = true; }
  }

  async function send() {
    if (busy) return;
    if (remoteStreaming) { stopRemoteGeneration(); return; }
    if (serverBusy) { toast("另一个对话正在生成，完成后再发送"); pollBusy(); return; }
    var text = input.value.trim();
    if ((!text && !pendingItems.length) || !modelReady) return;

    var sentAttachments = pendingItems.slice();
    input.value = ""; input.dispatchEvent(new Event("input"));
    busy = true; updateSendBtn();

    var user = addMsg("user");
    paintBody(user.body, text, []);
    appendFiles(user.body, sentAttachments);
    renderPending([]);

    var ai = addMsg("ai");
    var indicator = makeThinking(); ai.body.appendChild(indicator);
    var contentNode = el("div", "msg__content"); ai.body.appendChild(contentNode);
    var think = null, toolsAdded = false, thinkStart = 0;
    function dropIndicator() { if (indicator.parentNode) indicator.remove(); }

    var state = { text: "", think: "", sources: [], dirty: false,
                  thinkDirty: false, finished: false, stopped: false };
    // 批量重绘：流式 chunk 很密，120ms 合并一次，避免频繁解析 markdown
    var timer = setInterval(function () {
      if (state.thinkDirty) {
        state.thinkDirty = false; dropIndicator();
        if (!think) { thinkStart = Date.now(); think = addThinkDetails(ai.body, "", true, contentNode); }
        think.body.innerHTML = renderMarkdown(state.think); typeset(think.body);
        if (stick) scrollBottom();
      }
      if (state.dirty) {
        state.dirty = false; dropIndicator();
        paintBody(contentNode, state.text, state.sources);
        if (stick) scrollBottom();
      }
    }, 120);

    try {
      currentController = new AbortController();
      var resp = await fetch(api("/api/chat"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }), signal: currentController.signal
      });
      if (!resp.ok) { state.text = "**请求失败：** " + (await resp.text()).slice(0, 200); state.dirty = true; }
      else {
        var reader = resp.body.getReader(), dec = new TextDecoder(), buf = "";
        for (;;) {
          var r = await reader.read(); if (r.done) break;
          buf += dec.decode(r.value, { stream: true });
          var blocks = buf.split("\n\n"); buf = blocks.pop();
          blocks.forEach(function (b) { if (b.trim()) handleEvent(b, state); });
        }
        if (buf.trim()) handleEvent(buf, state);
      }
    } catch (e) {
      if (e && e.name === "AbortError") state.stopped = true;
      else { state.text += "\n\n**连接中断：** " + e; state.dirty = true; }
    }

    clearInterval(timer);
    dropIndicator();
    paintBody(contentNode, state.text || "（没有返回内容）", state.sources);
    finishThink(think, thinkStart ? Math.max(1, Math.round((Date.now() - thinkStart) / 1000)) : 0);
    renderSources(ai.col, state.sources);
    if (state.stopped) ai.col.appendChild(el("div", "stopped-note", "已停止生成"));

    // 用服务端历史校准索引；停止时服务端还要收尾，稍等再拉
    setTimeout(function () {
      getJSON("/api/history").then(function (d) {
        var turns = d.turns || [], last = turns.length ? turns[turns.length - 1] : null;
        loadConversations(true);
        renderPending(d.attachments || []);
        stateIndex = last ? last.index : -1;
        if (last && last.role === "assistant") {
          ai.msg.dataset.index = last.index;
          if (last.index > 0) {
            user.msg.dataset.index = last.index - 1;
            addUserEdit(user.col, user.msg, last.index - 1, text);
          }
          if (state.text && !toolsAdded) { toolsAdded = true; addTurnTools(ai.col, last.index, state.text, true); }
        }
        renderUsage(ai.col, state.usage);
      }).catch(function () {});
    }, state.stopped ? 500 : 0);

    if (stick) scrollBottom();
    busy = false; currentController = null; updateSendBtn();
    try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
  }

  function stopGeneration() {
    if (!busy) return;
    try { if (currentController) currentController.abort(); } catch (e) {}
    postJSON("/api/stop", {}).catch(function () {});
    setStatus("已停止生成");
  }
  function stopRemoteGeneration() {
    postJSON("/api/stop", {}).catch(function () {});
    setStatus("正在停止…"); pollBusy();
  }
  function setRemoteStreaming(on) { remoteStreaming = on; updateSendBtn(); }
  function pollBusy() {
    if (busyTimer) return;
    busyTimer = setInterval(function () {
      getJSON("/api/conversations").then(function (d) {
        serverBusy = !!d.busy; updateSendBtn();
        if (!serverBusy) { clearInterval(busyTimer); busyTimer = null; }
      }).catch(function () {});
    }, 2000);
  }
  function stopStreamPoll() { if (streamPoll) { clearInterval(streamPoll); streamPoll = null; } }
  function startStreamPoll() { if (!streamPoll) streamPoll = setInterval(refreshStreaming, 1500); }
  // 刷新后恢复「生成中」：轮询历史，把仍在流式的那条补全
  function refreshStreaming() {
    if (!currentConv) return;
    getJSON("/api/history").then(function (d) {
      if (d.id && d.id !== currentConv) return;
      var turns = d.turns || [], last = turns.length ? turns[turns.length - 1] : null;
      if (!last) { stopStreamPoll(); return; }
      var node = stage.querySelector('.msg[data-index="' + last.index + '"]');
      if (node) {
        var c = node.querySelector(".msg__content");
        if (c && last.content) paintBody(c, last.content, last.sources || []);
        var tb = node.querySelector(".msg__think-body");
        if (tb && last.reasoning) {
          tb.innerHTML = renderMarkdown(last.reasoning); typeset(tb);
          var dd = node.querySelector("details.msg__think"); if (dd) dd.hidden = false;
        }
        if (stick) scrollBottom();
      }
      if (!last.streaming) {
        stopStreamPoll(); setRemoteStreaming(false);
        if (node) {
          node.classList.remove("is-streaming");
          var badge = node.querySelector(".streaming-badge"); if (badge) badge.remove();
        }
        loadConversations(true);
      }
    }).catch(function () {});
  }

  // ------------------------------------------------------------ 重新生成 / 编辑 / 通用流式
  // 把一次 SSE 回答流式渲染进指定气泡（regenerate / edit 共用，逻辑与 send 一致）
  function streamReply(url, body, ai, onDone) {
    var indicator = makeThinking(); ai.body.appendChild(indicator);
    var contentNode = el("div", "msg__content"); ai.body.appendChild(contentNode);
    var think = null, thinkStart = 0;
    var state = { text: "", think: "", sources: [], dirty: false,
                  thinkDirty: false, finished: false, stopped: false };
    function drop() { if (indicator.parentNode) indicator.remove(); }
    var timer = setInterval(function () {
      if (state.thinkDirty) {
        state.thinkDirty = false; drop();
        if (!think) { thinkStart = Date.now(); think = addThinkDetails(ai.body, "", true, contentNode); }
        think.body.innerHTML = renderMarkdown(state.think); typeset(think.body);
        if (stick) scrollBottom();
      }
      if (state.dirty) {
        state.dirty = false; drop();
        paintBody(contentNode, state.text, state.sources);
        if (stick) scrollBottom();
      }
    }, 120);
    currentController = new AbortController();
    fetch(api(url), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: currentController.signal
    }).then(function (resp) {
      if (!resp.ok)
        return resp.text().then(function (t) { state.text = "**请求失败：** " + t.slice(0, 200); state.dirty = true; });
      var reader = resp.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { if (buf.trim()) handleEvent(buf, state); return; }
          buf += dec.decode(r.value, { stream: true });
          var blocks = buf.split("\n\n"); buf = blocks.pop();
          blocks.forEach(function (b) { if (b.trim()) handleEvent(b, state); });
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      if (e && e.name === "AbortError") state.stopped = true;
      else { state.text += "\n\n**连接中断：** " + e; state.dirty = true; }
    }).then(function () {
      clearInterval(timer); drop();
      paintBody(contentNode, state.text || "（没有返回内容）", state.sources);
      finishThink(think, thinkStart ? Math.max(1, Math.round((Date.now() - thinkStart) / 1000)) : 0);
      renderSources(ai.col, state.sources);
      renderUsage(ai.col, state.usage);
      if (state.stopped) ai.col.appendChild(el("div", "stopped-note", "已停止生成"));
      if (stick) scrollBottom();
      onDone(state);
    });
  }
  function afterRewrite() {
    busy = false; currentController = null; updateSendBtn();
    loadHistory(); loadConversations(true);
    try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
  }
  // 重新生成：丢掉末条回答，用同一个问题再答一次（服务端截断重跑）
  function regenerate(index) {
    if (busy || remoteStreaming) { stopGeneration(); return; }
    if (serverBusy) { toast("另一个对话正在生成，完成后再试"); return; }
    var old = stage.querySelector('.msg[data-index="' + index + '"]');
    if (old) old.remove();
    busy = true; updateSendBtn();
    var ai = addMsg("ai");
    streamReply("/api/regenerate", { index: index }, ai, afterRewrite);
  }
  // 编辑用户消息：内联编辑器，改完截断重发
  function startEdit(msg, index, content) {
    if (busy || remoteStreaming || serverBusy) { toast("正在生成中，稍后再编辑"); return; }
    var contentNode = msg.querySelector(".msg__content");
    if (!contentNode || msg.querySelector(".msg-edit")) return;
    var editor = el("div", "msg-edit");
    var ta = el("textarea", "msg-edit__input"); ta.value = content || ""; ta.rows = 3;
    var actions = el("div", "msg-edit__actions");
    var save = el("button", "msg-tool", "保存并重新发送"); save.type = "button";
    var cancel = el("button", "msg-tool", "取消"); cancel.type = "button";
    actions.appendChild(save); actions.appendChild(cancel);
    editor.appendChild(ta); editor.appendChild(actions);
    contentNode.style.display = "none";
    contentNode.parentNode.insertBefore(editor, contentNode.nextSibling);
    ta.focus();
    function close() { editor.remove(); contentNode.style.display = ""; }
    cancel.addEventListener("click", close);
    save.addEventListener("click", function () {
      var text = ta.value.trim();
      if (!text) { toast("消息不能为空"); return; }
      close(); submitEdit(msg, index, text);
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save.click(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
  }
  function submitEdit(msg, index, text) {
    var contentNode = msg.querySelector(".msg__content");
    if (contentNode) paintBody(contentNode, text, []);
    // 丢弃这条之后的所有气泡：服务端会截断到本条再重跑
    while (msg.nextSibling) msg.nextSibling.remove();
    busy = true; updateSendBtn();
    var ai = addMsg("ai");
    streamReply("/api/edit", { index: index, message: text }, ai, afterRewrite);
  }

  // ------------------------------------------------------------ 粘贴 / 拖拽附件 + 快捷键
  function uploadFiles(files) {
    (async function () { for (var i = 0; i < files.length; i++) await uploadOne(files[i]); })();
  }
  var dropHint = null;
  function showDropHint() {
    if (!dropHint) { dropHint = el("div", "drop-hint", "松开以上传附件"); document.body.appendChild(dropHint); }
    dropHint.classList.add("is-visible");
  }
  function hideDropHint() { if (dropHint) dropHint.classList.remove("is-visible"); }
  function bindComposerExtras() {
    // 粘贴图片/文件：有文件才拦截，纯文本粘贴照常进输入框
    document.addEventListener("paste", function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [];
      var files = [];
      for (var i = 0; i < items.length; i++)
        if (items[i].kind === "file") { var f = items[i].getAsFile(); if (f) files.push(f); }
      if (files.length) { e.preventDefault(); uploadFiles(files); }
    });
    function onDrop(e) {
      var files = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || []);
      hideDropHint();
      if (!files.length) return;
      e.preventDefault(); uploadFiles(files);
    }
    [stage, composerDock].forEach(function (zone) {
      if (!zone) return;
      zone.addEventListener("dragover", function (e) { e.preventDefault(); showDropHint(); });
      zone.addEventListener("dragleave", function (e) { if (e.target === zone) hideDropHint(); });
      zone.addEventListener("drop", onDrop);
    });
    document.addEventListener("dragend", hideDropHint);
    // Esc 停止生成（官方客户端通用快捷键）
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (busy) stopGeneration();
      else if (remoteStreaming) stopRemoteGeneration();
    });
  }

  // ------------------------------------------------------------ 历史恢复
  function renderHistoryTurn(t, isLast) {
    var node = addMsg(t.role, t.index);
    node.msg._turn = t;
    node.msg._isLast = isLast;
    if (t.reasoning) addThinkDetails(node.body, t.reasoning, !!t.streaming);
    var c = el("div", "msg__content"); node.body.appendChild(c);
    paintBody(c, t.content || "", t.sources || []);
    appendFiles(node.body, t.attachments);
    if (t.streaming) {
      node.msg.classList.add("is-streaming");
      node.col.appendChild(el("div", "streaming-badge", "生成中…"));
    }
    if (t.role === "assistant") {
      renderSources(node.col, t.sources || []);
      if (!t.streaming) addVersionSwitcher(node.col, t);
      if (t.content) addTurnTools(node.col, t.index, t.content, isLast && !t.streaming);
      renderUsage(node.col, t.usage);
    } else if (!t.streaming) {
      addUserEdit(node.col, node.msg, t.index, t.content || "");
    }
    return node;
  }
  // 重新生成的多版本对比：‹ n/m › 切换器（官方客户端同款）
  function addVersionSwitcher(col, t) {
    var versions = t.versions || [];
    if (versions.length < 2) return null;
    var cur = t.version || 0;
    var wrap = el("div", "msg-versions");
    var prev = iconBtn("上一个版本", ICONS.chevL);
    var label = el("span", "msg-ver-label", (cur + 1) + "/" + versions.length);
    var next = iconBtn("下一个版本", ICONS.chevR);
    prev.disabled = cur <= 0;
    next.disabled = cur >= versions.length - 1;
    prev.addEventListener("click", function () { switchVersion(t.index, cur - 1); });
    next.addEventListener("click", function () { switchVersion(t.index, cur + 1); });
    wrap.appendChild(prev); wrap.appendChild(label); wrap.appendChild(next);
    col.appendChild(wrap);
    return wrap;
  }
  function switchVersion(index, v) {
    var old = stage.querySelector('.msg[data-index="' + index + '"]');
    if (!old || !old._turn) return;
    var t = old._turn;
    var versions = t.versions || [];
    if (v < 0 || v >= versions.length) return;
    var ver = versions[v];
    var nt = Object.assign({}, t, { version: v, content: ver.content,
                                    reasoning: ver.reasoning, sources: ver.sources,
                                    usage: ver.usage });
    var savedStick = stick; stick = false;      // 原地替换，不要跳到末尾
    var node = renderHistoryTurn(nt, old._isLast);
    stick = savedStick;
    old.replaceWith(node.msg);
    postJSON("/api/version", { index: index, version: v }).catch(function () {});
  }
  function renderTurns(turns) {
    stage.innerHTML = ""; msgList = null;
    if (!turns || !turns.length) { showGreeting(); stateIndex = -1; refreshSelectedCount(); return; }
    // 只有末条 assistant 给「重新生成」：截断式重跑只作用在最新回答上
    var lastAi = -1;
    turns.forEach(function (t) { if (t.role === "assistant") lastAi = t.index; });
    turns.forEach(function (t) { renderHistoryTurn(t, t.index === lastAi); });
    stateIndex = turns[turns.length - 1].index;
    refreshSelectedCount();
    scrollBottom(true);
  }
  function loadHistory(focusIndex) {
    getJSON("/api/history").then(function (d) {
      if (d.id) currentConv = d.id;
      renderTurns(d.turns || []);
      renderConversations(conversations);
      renderPending(d.attachments || []);
      var turns = d.turns || [], last = turns.length ? turns[turns.length - 1] : null;
      if (last && last.streaming) { setRemoteStreaming(true); startStreamPoll(); }
      else { setRemoteStreaming(false); stopStreamPoll(); }
      setTitle(turns.length ? "对话" : "新对话");
      if (focusIndex != null) {
        var target = stage.querySelector('.msg[data-index="' + focusIndex + '"]');
        if (target) {
          scrollWithin(stage, target, Math.max(0, (stage.clientHeight - target.clientHeight) / 2));
          keepRootStill();
          target.classList.add("flash");
          setTimeout(function () { target.classList.remove("flash"); }, 1600);
        }
      }
    }).catch(function () {});
  }

  // ------------------------------------------------------------ 会话列表 / 搜索 / 管理
  function ensureHistList() {
    if (!histList || !histList.parentNode) {
      histList = el("div", "hist-list");
      panelHistory.appendChild(histList);
    }
    return histList;
  }
  function renderConversations(items) {
    conversations = items || [];
    var keyword = (histSearch && histSearch.value || "").trim().toLowerCase();
    if (!keyword) setSearchMeta("");
    var list = ensureHistList();
    list.innerHTML = "";
    if (!conversations.length) {
      list.hidden = true;
      if (histEmpty && !histEmpty.parentNode) panelHistory.appendChild(histEmpty);
      return;
    }
    list.hidden = false;
    if (histEmpty && histEmpty.parentNode) histEmpty.remove();
    var shown = conversations.filter(function (c) {
      return !keyword || (c.title || "").toLowerCase().indexOf(keyword) >= 0;
    });
    if (!shown.length) { list.appendChild(el("div", "hist-empty", "没有匹配的对话")); return; }
    shown.forEach(function (conv) { list.appendChild(buildHistItem(conv)); });
  }
  function buildHistItem(conv) {
    var row = el("div", "hist-item" + (conv.id === currentConv ? " is-active" : "")
                          + (convSelected[conv.id] ? " is-selected" : ""));
    row.tabIndex = 0;
    if (convManaging) {
      var box = el("input", "hist-check"); box.type = "checkbox";
      box.checked = !!convSelected[conv.id];
      box.addEventListener("click", function (e) { e.stopPropagation(); toggleConvSelect(conv.id); });
      row.appendChild(box);
    }
    var main = el("div", "hist-item__main");
    main.appendChild(el("span", "hist-item__title", conv.title || "新对话"));
    main.appendChild(el("span", "hist-item__meta", (conv.count || 0) + " 条消息"));
    row.appendChild(main);
    var actions = el("span", "hist-item__actions");
    var ren = el("button", null, "✎"); ren.type = "button"; ren.title = "重命名";
    ren.addEventListener("click", function (e) { e.stopPropagation(); renameConversation(conv); });
    var rm = el("button", null, "🗑"); rm.type = "button"; rm.title = "删除对话";
    rm.addEventListener("click", function (e) { e.stopPropagation(); deleteConversation(conv); });
    actions.appendChild(ren); actions.appendChild(rm);
    row.appendChild(actions);
    row.addEventListener("click", function () {
      if (convManaging) { toggleConvSelect(conv.id); return; }
      switchConversation(conv.id);
    });
    return row;
  }
  function toggleConvSelect(cid) {
    if (convSelected[cid]) delete convSelected[cid]; else convSelected[cid] = true;
    renderConversations(conversations); refreshConvBar();
  }
  function refreshConvBar() {
    var n = Object.keys(convSelected).length;
    if (histDeleteSel) { histDeleteSel.textContent = "删除选中 (" + n + ")"; histDeleteSel.disabled = n === 0; }
  }
  function setConvManaging(on) {
    convManaging = on; convSelected = {};
    if (histManageNormal) histManageNormal.hidden = on;
    if (histManageOn) histManageOn.hidden = !on;
    renderConversations(conversations); refreshConvBar();
  }
  function loadConversations(keepActive) {
    getJSON("/api/conversations").then(function (d) {
      if (!keepActive || !currentConv) currentConv = d.active || "";
      serverBusy = !!d.busy; updateSendBtn();
      if (serverBusy) pollBusy();
      renderConversations(d.items || []);
    }).catch(function () {});
  }
  function switchConversation(cid, focusIndex) {
    // 切走时原会话还在生成：断开本地连接（服务端继续跑完），标记忙
    if (busy) {
      try { if (currentController) currentController.abort(); } catch (e) {}
      busy = false; currentController = null; serverBusy = true; pollBusy();
    } else if (remoteStreaming) { serverBusy = true; pollBusy(); }
    stopStreamPoll();
    var same = (cid === currentConv);
    currentConv = cid;
    if (!same) loadConversations(true);
    loadHistory(focusIndex);
  }
  function renameConversation(conv) {
    var title = prompt("重命名对话", conv.title || "新对话");
    if (title === null) return;
    title = title.trim(); if (!title) return;
    postJSON("/api/conversations/rename", { id: conv.id, title: title })
      .then(function (d) {
        renderConversations(d.items || []);
        if (conv.id === currentConv) setTitle(title);
      }).catch(function () {});
  }
  function deleteConversation(conv) {
    if (!confirm("确定删除对话「" + (conv.title || "新对话") + "」吗？此操作不可恢复。")) return;
    postJSON("/api/conversations/delete", { id: conv.id }).then(function (d) {
      var items = d.items || [];
      if (conv.id === currentConv) { currentConv = d.active || (items[0] && items[0].id) || ""; loadHistory(); }
      renderConversations(items);
    }).catch(function () {});
  }
  function highlight(text, keyword) {
    var safe = escapeHtml(text);
    if (!keyword) return safe;
    var esc = escapeHtml(keyword).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return safe.replace(new RegExp("(" + esc + ")", "gi"), "<mark>$1</mark>");
  }
  function searchAll(keyword) {
    lastSearch = keyword;
    getJSON("/api/search?q=" + encodeURIComponent(keyword)).then(function (d) {
      if (keyword !== (histSearch.value || "").trim()) return;   // 结果过期
      renderSearchHits(d.items || [], keyword);
    }).catch(function () {});
  }
  function renderSearchHits(items, keyword) {
    var list = ensureHistList();
    list.hidden = false; list.innerHTML = "";
    if (histEmpty && histEmpty.parentNode) histEmpty.remove();
    setSearchMeta(items.length ? ("全文命中 " + items.length + " 条 · Enter 打开第一条") : "");
    if (!items.length) { list.appendChild(el("div", "hist-empty", "没有匹配「" + keyword + "」的对话")); return; }
    items.forEach(function (hit) {
      var row = el("div", "hist-hit" + (hit.id === currentConv ? " is-active" : ""));
      var title = el("div", "hit-title");
      title.innerHTML = highlight(hit.title, hit.title_hit ? keyword : "");
      row.appendChild(title);
      if (hit.match) {
        var sn = el("div", "hit-snippet");
        sn.innerHTML = highlight(hit.match.snippet, keyword);
        row.appendChild(sn);
      }
      row.addEventListener("click", function () {
        switchConversation(hit.id, hit.match ? hit.match.index : null);
      });
      list.appendChild(row);
    });
    // 面板可能比结果短：把命中滚进可视区（仅面板内滚动，避免带动整页）
    if (items.length) { scrollWithin(panelHistory, list, 8); keepRootStill(); }
  }
  function syncSearchClear() { if (histSearchClear) histSearchClear.hidden = !histSearch.value; }

  // ------------------------------------------------------------ 消息删除 / 管理 / 清空
  function deleteTurns(indexes) {
    indexes = indexes.filter(function (i) { return !isNaN(i); });
    if (!indexes.length) return;
    var label = indexes.length > 1 ? ("这 " + indexes.length + " 条对话") : "这条对话";
    if (!confirm("确定删除" + label + "吗？（问答会一起删掉）")) return;
    postJSON("/api/delete_batch", { indexes: indexes }).then(function (d) {
      renderTurns(d.turns || []);
      setStatus("已删除"); loadConversations(true);
    }).catch(function () {});
  }
  function refreshSelectedCount() {
    var n = stage.querySelectorAll(".msg-check:checked").length;
    if (topbarDeleteSel) { topbarDeleteSel.textContent = "删除选中 (" + n + ")"; topbarDeleteSel.disabled = n === 0; }
  }
  function setManaging(on) {
    managing = on;
    stage.classList.toggle("managing", on);
    if (topbarNormal) topbarNormal.hidden = on;
    if (topbarManage) topbarManage.hidden = !on;
    if (!on) Array.prototype.forEach.call(stage.querySelectorAll(".msg-check"), function (b) { b.checked = false; });
    refreshSelectedCount();
  }
  function resetConversation() {
    if (!confirm("确定清空这段对话吗？此操作不可恢复。")) return;
    postRaw("/api/reset", {}).catch(function () {});
    showGreeting(); stateIndex = -1; renderPending([]);
    setManaging(false); loadConversations(true); setStatus("已清空");
  }
  function exportAll() {
    var fmt = (topbarFmt && topbarFmt.value) || "md";
    postRaw("/api/export_all", { fmt: fmt })
      .then(function (resp) { return downloadResponse(resp, "对话导出." + fmt); })
      .catch(function (e) { toast("导出失败：" + e); });
  }

  // ------------------------------------------------------------ 配置 / 开关同步
  function setLevelText(txt) {
    levelTool.querySelector(".tool__value-text").textContent = txt;
    Array.prototype.forEach.call(levelTool.querySelectorAll(".popover__option"), function (o) {
      var on = (o.dataset.value || "") === txt;
      o.classList.toggle("is-selected", on);
      o.setAttribute("aria-selected", String(on));
    });
  }
  function statusText(cfg) {
    var bits = [cfg.model || "未选择模型"];
    if (cfg.search) bits.push("联网搜索");
    if (cfg.image) bits.push("文生图");
    if (cfg.reasoning_supported !== false && cfg.reasoning && cfg.reasoning !== "off")
      bits.push("推理" + (REASONING_TEXT[cfg.reasoning] || ""));
    return bits.join(" · ");
  }
  function updateStatus(cfg) {
    setStatus(cfg.ready === false ? ("配置不完整：" + (cfg.reason || "")) : statusText(cfg));
  }
  // 收起上拉菜单：外壳的 closeAllMenus 在它自己的闭包里，这里自己实现一份
  function closeMenus() {
    Array.prototype.forEach.call(document.querySelectorAll(".tool--menu"), function (tool) {
      tool.classList.remove("is-open");
      var pop = tool.querySelector(".popover");
      if (pop) pop.classList.remove("is-open");
      var tg = tool.querySelector(".tool__trigger");
      if (tg) tg.setAttribute("aria-expanded", "false");
    });
  }
  // 模型菜单：列出客户端已配置的模型，点一个就把客户端的勾同步过去
  function buildModelMenu(cfg) {
    var names = cfg.models || [];
    var pop = modelTool.querySelector(".popover");
    pop.innerHTML = "";
    pop.appendChild(el("p", "popover__title", names.length ? "选择模型" : "客户端还没有模型"));
    names.forEach(function (name) {
      var on = name === currentModel;
      var opt = el("button", "popover__option" + (on ? " is-selected" : ""), name);
      opt.type = "button";
      opt.setAttribute("role", "option");
      opt.setAttribute("aria-selected", String(on));
      opt.dataset.value = name;
      // 勾选图标：外壳用 svg，而 sanitizeHtml 会删 svg，所以只能 createElementNS 手工建
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "popover__check");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M5 12.5l4.5 4.5L19 7");
      svg.appendChild(path);
      opt.appendChild(svg);
      opt.addEventListener("click", function () { selectModel(name); });
      pop.appendChild(opt);
    });
    modelTool.querySelector(".tool__value-text").textContent = currentModel || "未选择模型";
    modelTool.querySelector(".tool__trigger").disabled = !names.length;
    modelTool.title = names.length ? "" : "请先在客户端「API 配置」添加模型";
  }
  function selectModel(name) {
    closeMenus();
    if (!name || name === currentModel) return;
    postRaw("/api/model", { model: name }).then(function (resp) {
      if (!resp.ok)
        return resp.text().then(function (t) { throw new Error(t || "切换失败"); });
      return resp.json();
    }).then(function (cfg) {
      applyConfig(cfg);
      toast("已切换到 " + name);
    }).catch(function (e) { toast("模型切换失败：" + (e.message || e)); });
  }
  function applyConfig(cfg) {
    currentModel = cfg.model || "";
    modelReady = !!cfg.model && cfg.ready !== false;

    buildModelMenu(cfg);

    // 联网搜索：未配置 KEY 时强制关闭并禁用（配置期 gating）
    var avail = cfg.search_available !== false;
    setOn(searchTool, !!cfg.search && avail);
    searchTool.querySelector(".tool__trigger").disabled = !avail;
    searchTool.title = avail ? "" : "客户端未配置搜索 KEY，无法启用联网搜索";

    // 思考 / 推理等级：模型不支持时禁用
    var supported = cfg.reasoning_supported !== false;
    var reason = cfg.reasoning || "off";
    setOn(thinkTool, supported && reason !== "off");
    thinkTool.querySelector(".tool__trigger").disabled = !supported;
    levelTool.querySelector(".tool__trigger").disabled = !supported;
    if (supported && reason !== "off") setLevelText(REASON_TO_LEVEL[reason] || "中");

    updateStatus(cfg); updateSendBtn();
  }
  function loadConfig(retries) {
    retries = retries || 0;
    getJSON("/api/config").then(function (cfg) {
      applyConfig(cfg);
      // 能力探测异步：还没出结果时稍后重拉
      if (cfg.reasoning_probe === "pending" && retries < 3)
        setTimeout(function () { loadConfig(retries + 1); }, 3000);
    }).catch(function () { setStatus("配置读取失败"); });
  }

  // ------------------------------------------------------------ 附件上传
  var fileInput = el("input");
  fileInput.type = "file"; fileInput.multiple = true; fileInput.hidden = true;
  fileInput.accept = ".png,.jpg,.jpeg,.webp,.bmp,.gif,.pdf,.md,.txt,.docx,.csv,.json";
  document.body.appendChild(fileInput);
  // 「+」收进命令框工具条左端，和官方附件入口一致
  var attachToolbar = document.querySelector(".toolbar");
  attachToolbar.insertBefore(addFileBtn, attachToolbar.firstChild);

  function renderPending(items) {
    pendingItems = items || [];
    Array.prototype.forEach.call(fileStrip.querySelectorAll(".file-chip"), function (c) { c.remove(); });
    pendingItems.forEach(function (item) {
      var chip = attachChip((item.kind === "image" ? "🖼 " : "📄 ") + item.name,
                            item.openable !== false, { pending: item.id });
      var del = el("button", "file-chip__del", "✕"); del.type = "button"; del.title = "移除";
      // 阻止冒泡：否则点 ✕ 会顺带触发 chip 上的「用系统应用打开」
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        postJSON("/api/attachments/remove", { id: item.id })
          .then(function (d) { renderPending(d.items); }).catch(function () {});
      });
      chip.appendChild(del);
      fileStrip.appendChild(chip);
    });
    fileStrip.classList.toggle("is-empty", pendingItems.length === 0);
  }
  function uploadOne(file) {
    return new Promise(function (resolve) {
      var reader = new FileReader();
      reader.onload = function () {
        var bytes = new Uint8Array(reader.result), binary = "";
        for (var i = 0; i < bytes.length; i += 8192)
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
        postRaw("/api/upload", { name: file.name, data: btoa(binary) }).then(function (r) {
          if (!r.ok) return r.text().then(function (t) { toast(file.name + "：" + t); });
          return r.json().then(function (d) { renderPending(d.items); });
        }).then(resolve, resolve);
      };
      reader.onerror = function () { resolve(); };
      reader.readAsArrayBuffer(file);
    });
  }
  fileInput.addEventListener("change", function () {
    var files = Array.prototype.slice.call(fileInput.files || []);
    fileInput.value = "";
    (async function () { for (var i = 0; i < files.length; i++) await uploadOne(files[i]); })();
  });

  // ------------------------------------------------------------ 注入控件：顶部状态栏 / 全局操作
  function buildTopbar() {
    var bar = el("div", "chat-topbar");
    statusEl = el("div", "chat-status", "连接中…");
    bar.appendChild(statusEl);

    topbarNormal = el("div", "topbar-actions");
    topbarFmt = el("select", "tb-fmt"); topbarFmt.title = "导出格式";
    EXPORT_FORMATS.forEach(function (pair) {
      var o = el("option", null, pair[1]); o.value = pair[0]; topbarFmt.appendChild(o);
    });
    var exp = el("button", "tb-btn", "导出对话"); exp.type = "button";
    exp.addEventListener("click", exportAll);
    var man = el("button", "tb-btn", "管理"); man.type = "button"; man.title = "批量选择并删除消息";
    man.addEventListener("click", function () { setManaging(true); });
    var clr = el("button", "tb-btn tb-danger", "清空"); clr.type = "button"; clr.title = "清空这段对话";
    clr.addEventListener("click", resetConversation);
    topbarNormal.appendChild(topbarFmt);
    topbarNormal.appendChild(exp);
    topbarNormal.appendChild(man);
    topbarNormal.appendChild(clr);
    bar.appendChild(topbarNormal);

    topbarManage = el("div", "topbar-actions"); topbarManage.hidden = true;
    topbarDeleteSel = el("button", "tb-btn tb-danger", "删除选中 (0)"); topbarDeleteSel.type = "button";
    topbarDeleteSel.disabled = true;
    topbarDeleteSel.addEventListener("click", function () {
      var indexes = Array.prototype.map.call(stage.querySelectorAll(".msg-check:checked"),
        function (b) { return Number(b.closest(".msg").dataset.index); });
      deleteTurns(indexes); setManaging(false);
    });
    var cancel = el("button", "tb-btn", "取消"); cancel.type = "button";
    cancel.addEventListener("click", function () { setManaging(false); });
    topbarManage.appendChild(topbarDeleteSel);
    topbarManage.appendChild(cancel);
    bar.appendChild(topbarManage);

    mainEl.insertBefore(bar, mainEl.firstChild);
  }

  // ------------------------------------------------------------ 注入控件：历史搜索 / 会话管理
  function buildHistoryTools() {
    var tools = el("div", "hist-tools");
    var wrap = el("div", "hist-search-wrap");
    var icon = el("span", "hist-search-icon"); icon.innerHTML = ICONS.search;
    histSearch = el("input", "hist-search"); histSearch.type = "text";
    histSearch.placeholder = "搜索对话…（/ 聚焦）";
    histSearch.setAttribute("aria-label", "搜索对话");
    histSearchClear = el("button", "hist-search-clear", "✕"); histSearchClear.type = "button";
    histSearchClear.title = "清空搜索"; histSearchClear.hidden = true;
    wrap.appendChild(icon); wrap.appendChild(histSearch); wrap.appendChild(histSearchClear);
    tools.appendChild(wrap);
    histSearchMeta = el("div", "hist-search-meta");
    tools.appendChild(histSearchMeta);

    histManageNormal = el("div", "hist-manage");
    var man = el("button", "tb-btn tb-sm", "管理"); man.type = "button";
    man.addEventListener("click", function () { setConvManaging(true); });
    histManageNormal.appendChild(man);
    tools.appendChild(histManageNormal);

    histManageOn = el("div", "hist-manage"); histManageOn.hidden = true;
    histDeleteSel = el("button", "tb-btn tb-sm tb-danger", "删除选中 (0)"); histDeleteSel.type = "button";
    histDeleteSel.disabled = true;
    histDeleteSel.addEventListener("click", function () {
      var ids = Object.keys(convSelected); if (!ids.length) return;
      if (!confirm("确定删除选中的 " + ids.length + " 个对话吗？此操作不可恢复。")) return;
      postJSON("/api/conversations/delete_batch", { ids: ids }).then(function (d) {
        var items = d.items || [];
        if (ids.indexOf(currentConv) >= 0) { currentConv = d.active || (items[0] && items[0].id) || ""; loadHistory(); }
        setConvManaging(false); renderConversations(items);
      }).catch(function () {});
    });
    var de = el("button", "tb-btn tb-sm", "删除空对话"); de.type = "button";
    de.addEventListener("click", function () {
      if (!confirm("删除所有没有消息的对话？")) return;
      postJSON("/api/conversations/delete_empty", {}).then(function (d) {
        var items = d.items || [];
        currentConv = d.active || (items[0] && items[0].id) || "";
        setConvManaging(false); renderConversations(items); loadHistory();
        setStatus("已删除 " + (d.removed || 0) + " 个空对话");
      }).catch(function () {});
    });
    var cancel = el("button", "tb-btn tb-sm", "取消"); cancel.type = "button";
    cancel.addEventListener("click", function () { setConvManaging(false); });
    histManageOn.appendChild(histDeleteSel);
    histManageOn.appendChild(de);
    histManageOn.appendChild(cancel);
    tools.appendChild(histManageOn);

    var title = panelHistory.querySelector(".panel-title");
    if (title && title.nextSibling) panelHistory.insertBefore(tools, title.nextSibling);
    else panelHistory.appendChild(tools);

    histSearchClear.addEventListener("click", function () {
      histSearch.value = ""; syncSearchClear(); setSearchMeta("");
      renderConversations(conversations); histSearch.focus();
    });
    histSearch.addEventListener("input", function () {
      syncSearchClear();
      var keyword = histSearch.value.trim();
      renderConversations(conversations);          // 先按标题过滤，响应快
      if (searchTimer) clearTimeout(searchTimer);
      if (keyword.length < 2) {
        lastSearch = "";
        setSearchMeta(keyword ? "再输入一个字开始全文搜索" : "");
        return;
      }
      setSearchMeta("搜索中…", true);
      searchTimer = setTimeout(function () { searchAll(keyword); }, 250);
    });
    // Enter 跳第一条命中；Esc 清空并退出，符合搜索框直觉
    histSearch.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var first = histList && histList.querySelector(".hist-hit");
        if (first) first.click();
      } else if (e.key === "Escape") {
        histSearch.value = ""; syncSearchClear(); setSearchMeta("");
        renderConversations(conversations); histSearch.blur();
      }
    });
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      var typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "/" && !typing) { e.preventDefault(); histSearch.focus(); }
    });
  }
  function setSearchMeta(text, busy) {
    if (!histSearchMeta) return;
    histSearchMeta.textContent = text || "";
    histSearchMeta.classList.toggle("is-busy", !!busy);
  }

  // ------------------------------------------------------------ 注入控件：回到最新 / 侧边栏收起
  function buildJump() {
    jumpLatest = el("button", "jump-latest", "↓ 回到最新"); jumpLatest.type = "button";
    jumpLatest.hidden = true;
    document.body.appendChild(jumpLatest);
    jumpLatest.addEventListener("click", function () { scrollBottom(true); });
    function place() {
      var h = composerDock ? composerDock.offsetHeight : 160;
      jumpLatest.style.bottom = (h + 28) + "px";
    }
    place(); window.addEventListener("resize", place);
    stage.addEventListener("scroll", updateJump);
    window.addEventListener("resize", updateJump);
    setInterval(updateJump, 800);
  }
  function buildSidebarToggle() {
    var panelHeader = document.querySelector(".panel-header");
    var collapseBtn = el("button", "collapse-btn ui-collapse"); collapseBtn.type = "button";
    collapseBtn.title = "收起侧边栏";
    collapseBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';
    panelHeader.appendChild(collapseBtn);
    var expandBtn = el("button", "expand-btn ui-expand"); expandBtn.type = "button";
    expandBtn.title = "展开侧边栏";
    expandBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
    document.body.appendChild(expandBtn);
    collapseBtn.addEventListener("click", function () {
      document.body.classList.add("sidebar-collapsed");
      localStorage.setItem("starty-sidebar-collapsed", "1");
    });
    expandBtn.addEventListener("click", function () {
      document.body.classList.remove("sidebar-collapsed");
      localStorage.setItem("starty-sidebar-collapsed", "0");
    });
    if (localStorage.getItem("starty-sidebar-collapsed") === "1")
      document.body.classList.add("sidebar-collapsed");
  }
  // 侧边栏拖拽调宽：把手绝对定位在 sidebar 与 main 之间的间隙里，随宽度自动跟随。
  // 宽度写 inline flex-basis 覆盖外壳媒体查询；双击恢复默认；记住用户偏好。
  function buildSidebarResizer() {
    var sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;
    var grip = el("div", "sidebar-resizer");
    grip.setAttribute("role", "separator");
    grip.setAttribute("aria-orientation", "vertical");
    grip.setAttribute("aria-label", "拖动调整侧边栏宽度，双击恢复默认");
    grip.title = "拖动调整侧边栏宽度，双击恢复默认";
    grip.tabIndex = 0;
    sidebar.appendChild(grip);
    var MIN = 280;
    function maxW() {
      var app = document.querySelector(".app");
      return Math.min(640, Math.floor((app ? app.clientWidth : 1200) * 0.5));
    }
    function zoom() { return parseFloat(getComputedStyle(document.documentElement).zoom) || 1; }
    function apply(w) {
      w = Math.max(MIN, Math.min(maxW(), Math.round(w)));
      sidebar.style.flexBasis = w + "px";
      return w;
    }
    var saved = parseInt(localStorage.getItem("starty-sidebar-width") || "", 10);
    if (saved >= MIN) apply(saved);
    var dragging = false, startX = 0, startW = 0;
    grip.addEventListener("pointerdown", function (e) {
      dragging = true; startX = e.clientX; startW = sidebar.offsetWidth;
      try { grip.setPointerCapture(e.pointerId); } catch (err) {}
      document.body.classList.add("is-resizing");
      e.preventDefault();
    });
    grip.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      // clientX 是缩放后的视觉像素，除以 zoom 换回布局像素再累加
      apply(startW + (e.clientX - startX) / zoom());
    });
    function end() {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove("is-resizing");
      localStorage.setItem("starty-sidebar-width", String(sidebar.offsetWidth));
    }
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
    grip.addEventListener("dblclick", function () {
      sidebar.style.flexBasis = "";
      localStorage.removeItem("starty-sidebar-width");
    });
    grip.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
      if (!d) return;
      e.preventDefault();
      localStorage.setItem("starty-sidebar-width", String(apply(sidebar.offsetWidth + d)));
    });
  }
  // 深度视觉优化：把悬浮工具胶囊收进命令框、侧栏顶栏两端对齐。
  // 只挂新增类 + 移动既有节点，starty.html 本体保持逐字节不变。
  function polishShell() {
    var composer = document.querySelector(".composer");
    var toolbar = document.querySelector(".toolbar");
    if (composer && toolbar && toolbar.parentNode !== composer) {
      composer.appendChild(toolbar);
      composer.classList.add("ui-composer");
      toolbar.classList.add("ui-toolbar");
    }
    var panelHeader = document.querySelector(".panel-header");
    if (panelHeader) panelHeader.classList.add("ui-panel-header");
    // 给思考/联网开关加线性图标，配合 CSS 把拨动开关换成 ghost 高亮胶囊
    addToolIcon(thinkTool, ICONS.think);
    addToolIcon(searchTool, ICONS.search);
  }
  function addToolIcon(tool, svg) {
    if (!tool) return;
    var trigger = tool.querySelector(".tool__trigger");
    if (!trigger || trigger.querySelector(".tool__icon")) return;
    var s = el("span", "tool__icon"); s.innerHTML = svg;
    trigger.insertBefore(s, trigger.firstChild);
  }
  function mountKnowledgeButton() {
    var empty = panelKnowledge.querySelector(".empty-state");
    if (empty) empty.remove();
    var wrap = el("div", "kb-wrap");
    var open = el("button", "kb-btn", "📚 打开投喂台 / 管理知识库"); open.type = "button";
    open.addEventListener("click", function () {
      window.open("/kb?t=" + encodeURIComponent(pageToken()), "_blank");
    });
    var list = el("div", "kb-list");
    wrap.appendChild(open); wrap.appendChild(list);
    panelKnowledge.appendChild(wrap);
    refreshKbPanel(list);
  }
  function pageToken() { return new URLSearchParams(location.search).get("t") || ""; }
  function refreshKbPanel(list) {
    getJSON("/api/kb/bases").then(function (d) {
      list.innerHTML = "";
      var items = d.items || [];
      if (!items.length) {
        list.appendChild(el("p", "kb-empty-hint", "还没有知识库，点上方按钮投喂教材 / 知识点。"));
        return;
      }
      items.forEach(function (b) {
        var on = b.id === d.active;
        var row = el("div", "kb-row" + (on ? " is-active" : ""));
        row.appendChild(el("span", "kb-row-name", b.name));
        row.appendChild(el("span", "kb-row-meta", b.chunks + " 块"));
        var tog = el("button", "kb-row-toggle", on ? "已启用" : "启用"); tog.type = "button";
        tog.addEventListener("click", function () {
          postJSON("/api/kb/base/active", { id: on ? "" : b.id })
            .then(function () { refreshKbPanel(list); loadConfig(0); });
        });
        row.appendChild(tog);
        list.appendChild(row);
      });
    }).catch(function () {});
  }

  // ------------------------------------------------------------ 体验层：主题 / 灵动岛 / 光标光晕 / 模型人格
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("starty-theme", mode);
  }
  function initTheme() {
    var saved = localStorage.getItem("starty-theme");
    if (!saved) saved = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    applyTheme(saved);
  }

  var islandEl = null, islandTimer = null;
  function buildIsland() {
    islandEl = el("div", "island");
    islandEl.innerHTML = '<span class="island-dot"></span><span class="island-label"></span>';
    document.body.appendChild(islandEl);
    islandEl.addEventListener("click", function () { islandEl.classList.toggle("is-expanded"); });
  }
  function islandSet(text, live) {
    if (!islandEl) return;
    islandEl.querySelector(".island-label").textContent = text;
    islandEl.classList.toggle("is-live", !!live);
    islandEl.classList.add("is-visible");
    clearTimeout(islandTimer);
    if (!live) islandTimer = setTimeout(function () { islandEl.classList.remove("is-visible"); }, 2600);
  }

  var glowEl = null, glowRAF = 0, glowX = 0, glowY = 0;
  function glowEnabled() {
    return localStorage.getItem("starty-glow") === "1"
      && matchMedia("(hover: hover)").matches
      && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function buildGlow() {
    glowEl = el("div", "cursor-glow");
    document.body.appendChild(glowEl);
    document.addEventListener("pointermove", function (e) {
      if (!glowEnabled()) { glowEl.classList.remove("is-on"); return; }
      glowX = e.clientX; glowY = e.clientY;
      glowEl.classList.add("is-on");
      if (!glowRAF) glowRAF = requestAnimationFrame(function () {
        glowRAF = 0;
        glowEl.style.transform = "translate3d(" + glowX + "px," + glowY + "px,0)";
      });
    }, { passive: true });
  }

  function getPersona() {
    try { return JSON.parse(localStorage.getItem("starty-persona") || "{}"); }
    catch (e) { return {}; }
  }
  function avatarNode(avatar) {
    // 仅支持 emoji 头像；旧版上传的 dataURL 残留直接忽略，避免渲染出破图
    if (/^data:/i.test(avatar || "")) avatar = "";
    return el("span", "persona-avatar", avatar || "🤖");
  }
  function applyPersona() {
    var p = getPersona();
    var line = document.querySelector(".greeting-persona");
    if (line) {
      if (p.name || p.avatar) {
        line.hidden = false;
        line.innerHTML = "";
        line.appendChild(avatarNode(p.avatar));
        line.appendChild(el("span", null, "我是 " + (p.name || "STARTY") + "，你的学习伙伴"));
      } else line.hidden = true;
    }
  }
  function personaNode() {
    var p = getPersona();
    if (!p.name && !p.avatar) return null;
    var row = el("div", "msg-persona");
    row.appendChild(avatarNode(p.avatar));
    row.appendChild(el("span", "persona-name", p.name || "STARTY"));
    return row;
  }
  function buildProfilePanel() {
    var panel = el("div", "profile-panel");
    panel.hidden = true;
    var p = getPersona();
    panel.innerHTML =
      '<h3>个性化<span id="personaSaved" class="persona-saved"><span class="dot"></span>已自动保存</span></h3>' +
      '<label>助手昵称<input id="personaName" maxlength="12" placeholder="如「小知」"></label>' +
      '<label>头像（emoji）<div class="persona-emoji-row">' +
      [0x1F916, 0x1F9E0, 0x1F989, 0x1F431, 0x1F98A, 0x1F43C, 0x1F31F]
        .map(function (cp) { return String.fromCodePoint(cp); }).map(function (e) {
        return '<button class="persona-emoji" data-e="' + e + '">' + e + "</button>";
      }).join("") + '</div><input id="personaAvatar" maxlength="4" placeholder="自定义 emoji"></label>' +
      '<label class="profile-row">浅色模式<input type="checkbox" id="themeLight"></label>' +
      '<label class="profile-row">光标光晕<input type="checkbox" id="glowToggle"></label>';
    document.body.appendChild(panel);
    var nameI = panel.querySelector("#personaName");
    var avaI = panel.querySelector("#personaAvatar");
    var savedEl = panel.querySelector("#personaSaved");
    nameI.value = p.name || ""; avaI.value = (p.avatar && p.avatar.length <= 4) ? p.avatar : "";
    panel.querySelector("#themeLight").checked =
      document.documentElement.getAttribute("data-theme") === "light";
    panel.querySelector("#glowToggle").checked = localStorage.getItem("starty-glow") === "1";

    // 自动保存：任何编辑立即生效（输入防抖），不再需要手动点「保存」
    var savedTimer = null, nameTimer = null;
    function flashSaved() {
      savedEl.classList.add("is-on");
      clearTimeout(savedTimer);
      savedTimer = setTimeout(function () { savedEl.classList.remove("is-on"); }, 1600);
    }
    function markEmoji() {
      var cur = avaI.value.trim();
      panel.querySelectorAll(".persona-emoji").forEach(function (b) {
        b.classList.toggle("is-sel", b.getAttribute("data-e") === cur);
      });
    }
    function savePersona() {
      var avatar = avaI.value.trim();
      localStorage.removeItem("starty-persona-img");   // 清掉旧版上传头像残留
      localStorage.setItem("starty-persona", JSON.stringify({ name: nameI.value.trim(), avatar: avatar }));
      // 同步给后端注入系统提示：模型才会真的以这个名字自称，而不只是界面显示
      postJSON("/api/persona", { name: nameI.value.trim(), avatar: avatar }).catch(function () {});
      applyPersona(); markEmoji(); flashSaved();
    }
    function queueSave() { clearTimeout(nameTimer); nameTimer = setTimeout(savePersona, 400); }
    nameI.addEventListener("input", queueSave);
    avaI.addEventListener("input", queueSave);
    markEmoji();
    panel.querySelectorAll(".persona-emoji").forEach(function (b) {
      b.addEventListener("click", function () { avaI.value = b.getAttribute("data-e"); savePersona(); });
    });
    panel.querySelector("#themeLight").addEventListener("change", function () {
      applyTheme(this.checked ? "light" : "dark"); flashSaved();
    });
    panel.querySelector("#glowToggle").addEventListener("change", function () {
      localStorage.setItem("starty-glow", this.checked ? "1" : "0"); flashSaved();
    });
    profileBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      panel.hidden = !panel.hidden;
    });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && e.target !== profileBtn) panel.hidden = true;
    });
  }

  // ------------------------------------------------------------ 事件绑定
  function bindControls() {
    // 外壳内联脚本先切换 .is-on / 更新等级文字，这里在其后读最新状态再同步后端
    thinkTool.querySelector(".tool__trigger").addEventListener("click", function () {
      if (this.disabled) return;
      var on = isOn(thinkTool);
      postJSON("/api/setting", { reasoning: on ? (LEVEL_TO_REASON[levelText()] || "medium") : "off" })
        .then(updateStatus).catch(function () {});
    });
    searchTool.querySelector(".tool__trigger").addEventListener("click", function () {
      if (this.disabled) return;
      postJSON("/api/setting", { search: isOn(searchTool) }).then(updateStatus).catch(function () {});
    });
    Array.prototype.forEach.call(levelTool.querySelectorAll(".popover__option"), function (o) {
      o.addEventListener("click", function () {
        setOn(thinkTool, true);
        postJSON("/api/setting", { reasoning: LEVEL_TO_REASON[o.dataset.value] || "medium" })
          .then(updateStatus).catch(function () {});
      });
    });

    sendBtn.addEventListener("click", function () {
      if (sendBtn.disabled) return;
      if (busy) { stopGeneration(); return; }
      if (remoteStreaming) { stopRemoteGeneration(); return; }
      send();
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (busy) { stopGeneration(); return; }
        if (remoteStreaming) { stopRemoteGeneration(); return; }
        if (sendBtn.disabled) return;
        send();
      }
    });
    newChatBtn.addEventListener("click", function () {
      postJSON("/api/conversations/new", {}).then(function (d) {
        currentConv = d.id;
        showGreeting(); stateIndex = -1; renderPending([]);
        setManaging(false); renderConversations(d.items || []);
        setTitle("新对话"); input.focus();
      }).catch(function () {});
    });
    addFileBtn.addEventListener("click", function () { fileInput.click(); });
  }

  // ------------------------------------------------------------ 启动
  initTheme();
  buildTopbar();
  buildHistoryTools();
  buildJump();
  buildSidebarToggle();
  buildSidebarResizer();
  polishShell();
  mountKnowledgeButton();
  buildIsland();
  buildGlow();
  buildProfilePanel();
  postJSON("/api/persona", getPersona()).catch(function () {});   // 刷新后把人格同步回后端
  bindControls();
  bindComposerExtras();
  document.addEventListener("click", closeMsgMenus);   // 点空白处收起导出菜单
  renderPending([]);          // 清掉外壳演示用的 text.txt / text.jpg 占位 chip
  loadConfig(0);
  loadConversations(false);
  loadHistory();
  updateSendBtn();
  try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
})();