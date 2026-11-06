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
  var histSearch, histSearchClear, histList, histManageNormal, histManageOn, histDeleteSel;
  var jumpLatest;

  function toast(msg) {
    var t = el("div", "toast", msg);
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }
  function setStatus(msg) { if (statusEl) statusEl.textContent = msg; }
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
  function ensureList() {
    if (greeting && greeting.parentNode) greeting.remove();
    if (!msgList || !msgList.parentNode) {
      msgList = el("div", "msg-list");
      stage.appendChild(msgList);
    }
    stage.classList.add("has-msgs");
    return msgList;
  }
  function showGreeting() {
    stage.innerHTML = ""; msgList = null;
    stage.classList.remove("has-msgs", "managing");
    if (greeting) stage.appendChild(greeting);
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
  // 构建一条消息：复选框 + 头像 + (角色名 + 气泡 + 删除钮)，返回各节点引用
  function addMsg(role, index) {
    var list = ensureList();
    var msg = el("div", "msg " + (role === "user" ? "msg--user" : "msg--ai"));
    if (index != null) msg.dataset.index = index;
    var check = el("input", "msg-check"); check.type = "checkbox";
    check.addEventListener("change", refreshSelectedCount);
    msg.appendChild(check);
    var av = el("div", "msg__avatar", role === "user" ? "你" : "AI");
    msg.appendChild(av);
    var col = el("div", "msg__col");
    col.appendChild(el("div", "msg__role", role === "user" ? "你" : "STARTY"));
    var body = el("div", "msg__body");
    col.appendChild(body);
    var del = el("button", "msg-del", "×"); del.type = "button"; del.title = "删除这条对话";
    del.addEventListener("click", function () { deleteTurns([Number(msg.dataset.index)]); });
    col.appendChild(del);
    msg.appendChild(col);
    list.appendChild(msg);
    if (stick) scrollBottom();
    return { msg: msg, col: col, body: body };
  }
  function appendFiles(body, items) {
    if (!items || !items.length) return;
    var box = el("div", "msg__files");
    items.forEach(function (a) {
      box.appendChild(el("span", "file-chip", (a.kind === "image" ? "🖼 " : "📄 ") + (a.name || "")));
    });
    body.parentNode.appendChild(box);
  }
  function makeThinking() {
    var t = el("span", "thinking");
    t.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
                + '<path d="M12 3a9 9 0 1 0 9 9"/></svg><span>正在思考…</span>';
    return t;
  }
  function addThinkDetails(body, text, open) {
    var d = el("details", "msg__think"); d.open = (open !== false);
    d.appendChild(el("summary", null, "思考过程"));
    var b = el("div", "msg__think-body");
    b.innerHTML = renderMarkdown(text || ""); typeset(b);
    d.appendChild(b);
    body.appendChild(d);
    return { details: d, body: b };
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
  // 单条回答的导出工具条（MD / Word / PDF / Excel）
  function addTurnTools(col, index) {
    var bar = el("div", "msg-tools");
    EXPORT_FORMATS.forEach(function (pair) {
      var btn = el("button", "msg-tool", pair[1]); btn.type = "button";
      btn.addEventListener("click", function () {
        btn.disabled = true;
        postRaw("/api/export", { index: index, fmt: pair[0] })
          .then(function (resp) { return downloadResponse(resp, "answer." + pair[0]); })
          .catch(function (e) { toast("导出失败：" + e); })
          .then(function () { btn.disabled = false; });
      });
      bar.appendChild(btn);
    });
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
    var think = null, toolsAdded = false;
    function dropIndicator() { if (indicator.parentNode) indicator.remove(); }

    var state = { text: "", think: "", sources: [], dirty: false,
                  thinkDirty: false, finished: false, stopped: false };
    // 批量重绘：流式 chunk 很密，120ms 合并一次，避免频繁解析 markdown
    var timer = setInterval(function () {
      if (state.thinkDirty) {
        state.thinkDirty = false; dropIndicator();
        if (!think) think = addThinkDetails(ai.body, "", true);
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
          if (last.index > 0) user.msg.dataset.index = last.index - 1;
          if (state.text && !toolsAdded) { toolsAdded = true; addTurnTools(ai.col, last.index); }
        }
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

  // ------------------------------------------------------------ 历史恢复
  function renderHistoryTurn(t) {
    var node = addMsg(t.role, t.index);
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
      if (t.content) addTurnTools(node.col, t.index);
    }
    return node;
  }
  function renderTurns(turns) {
    stage.innerHTML = ""; msgList = null;
    if (!turns || !turns.length) { showGreeting(); stateIndex = -1; refreshSelectedCount(); return; }
    turns.forEach(renderHistoryTurn);
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
          target.scrollIntoView({ block: "center" });
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
    if (!items.length) { list.appendChild(el("div", "hist-empty", "没有匹配的对话")); return; }
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
  // 「+」移输入行常驻（附件条无附件时会隐藏）
  document.querySelector(".composer-row").insertBefore(addFileBtn, sendBtn);

  function renderPending(items) {
    pendingItems = items || [];
    Array.prototype.forEach.call(fileStrip.querySelectorAll(".file-chip"), function (c) { c.remove(); });
    pendingItems.forEach(function (item) {
      var chip = el("span", "file-chip");
      chip.appendChild(document.createTextNode((item.kind === "image" ? "🖼 " : "📄 ") + item.name));
      var del = el("button", "file-chip__del", "✕"); del.type = "button"; del.title = "移除";
      del.addEventListener("click", function () {
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
    histSearch = el("input", "hist-search"); histSearch.type = "text";
    histSearch.placeholder = "搜索对话…";
    histSearchClear = el("button", "hist-search-clear", "✕"); histSearchClear.type = "button";
    histSearchClear.title = "清空搜索"; histSearchClear.hidden = true;
    wrap.appendChild(histSearch); wrap.appendChild(histSearchClear);
    tools.appendChild(wrap);

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
      histSearch.value = ""; syncSearchClear(); renderConversations(conversations); histSearch.focus();
    });
    histSearch.addEventListener("input", function () {
      syncSearchClear();
      var keyword = histSearch.value.trim();
      renderConversations(conversations);          // 先按标题过滤，响应快
      if (searchTimer) clearTimeout(searchTimer);
      if (keyword.length < 2) { lastSearch = ""; return; }
      searchTimer = setTimeout(function () { searchAll(keyword); }, 250);
    });
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
    var collapseBtn = el("button", "collapse-btn", "⟨"); collapseBtn.type = "button";
    collapseBtn.title = "收起侧边栏";
    panelHeader.appendChild(collapseBtn);
    var expandBtn = el("button", "expand-btn", "⟩"); expandBtn.type = "button";
    expandBtn.title = "展开侧边栏";
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
  function mountKnowledgeButton() {
    var empty = panelKnowledge.querySelector(".empty-state");
    if (empty) empty.remove();
    var wrap = el("div", "kb-wrap");
    var kb = el("button", "kb-btn", "RAG 知识库（待接入）"); kb.type = "button";
    kb.addEventListener("click", function () { toast("知识库尚未搭建，接入后再开放"); });
    wrap.appendChild(kb); panelKnowledge.appendChild(wrap);
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
    profileBtn.addEventListener("click", function () {
      toast("模型：" + (currentModel || "未选择") + "（在客户端配置）");
    });
  }

  // ------------------------------------------------------------ 启动
  buildTopbar();
  buildHistoryTools();
  buildJump();
  buildSidebarToggle();
  mountKnowledgeButton();
  bindControls();
  renderPending([]);          // 清掉外壳演示用的 text.txt / text.jpg 占位 chip
  loadConfig(0);
  loadConversations(false);
  loadHistory();
  updateSendBtn();
  try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
})();