/* =========================================================================
   聊天窗口前端逻辑
   对外接口（由 Python 通过 runJavaScript 调用）：
     renderChat(turns, pending)   整体渲染历史消息
     beginStream()                开始一条流式消息
     streamAppend(kind, text)     kind: "body" | "think"
     streamSources(list)          流式中插入搜索来源
     resetStream()                丢弃当前流式消息（模型决定改为搜索时）
     endStream()                  结束流式消息
   ========================================================================= */

(function () {
  "use strict";

  var chat    = document.getElementById("chat");
  var jumpBtn = document.getElementById("jump-latest");

  /* =======================================================================
     1. 数学公式
     ======================================================================= */
  function renderMath(root) {
    renderMathInElement(root, {
      delimiters: [
        { left: "$$",  right: "$$",  display: true  },
        { left: "$",   right: "$",   display: false },
        { left: "\\[", right: "\\]", display: true  },
        { left: "\\(", right: "\\)", display: false }
      ],
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
    });
  }

  // marked 会把 \[ \( 当转义吃掉，导致 KaTeX 收不到定界符。
  // 渲染前统一转换成 $ / $$（代码块内保持原样）
  function normalizeMath(src) {
    var parts = src.split("```");
    for (var i = 0; i < parts.length; i += 2) {   // 偶数段 = 代码块之外
      parts[i] = parts[i].split("\\[").join("$$")
                         .split("\\]").join("$$")
                         .split("\\(").join("$")
                         .split("\\)").join("$");
    }
    return parts.join("```");
  }


  // 模型输出属于不可信内容：插入 DOM 前必须去掉脚本和事件属性，
  // 否则一段 <img onerror=...> 就能在页面里执行任意 JS（页面能调用本地 API）。
  var _BLOCKED_TAGS = ["script", "iframe", "object", "embed", "link", "meta",
                       "style", "base", "form", "input", "button", "textarea",
                       "select", "svg"];

  function sanitizeHtml(html) {
    if (!html) return "";
    var doc;
    try {
      doc = new DOMParser().parseFromString(html, "text/html");
    } catch (e) {
      return String(html).replace(/</g, "&lt;");
    }
    _BLOCKED_TAGS.forEach(function (tag) {
      Array.prototype.forEach.call(doc.querySelectorAll(tag), function (el) {
        el.parentNode && el.parentNode.removeChild(el);
      });
    });
    Array.prototype.forEach.call(doc.querySelectorAll("*"), function (el) {
      for (var i = el.attributes.length - 1; i >= 0; i--) {
        var attr = el.attributes[i];
        var name = attr.name.toLowerCase();
        var value = (attr.value || "").replace(/[\s\u0000]+/g, "").toLowerCase();
        var isEvent = name.indexOf("on") === 0;
        var isJsUrl = (name === "href" || name === "src" || name === "action"
                       || name === "xlink:href" || name === "formaction")
                      && (value.indexOf("javascript:") === 0
                          || value.indexOf("data:text/html") === 0
                          || value.indexOf("vbscript:") === 0);
        if (isEvent || isJsUrl) el.removeAttribute(attr.name);
      }
      if (el.tagName === "A" && el.getAttribute("href")) {
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      }
    });
    return doc.body.innerHTML;
  }

  var _markedParse = marked.parse;
  marked.parse = function (src) { return sanitizeHtml(_markedParse(normalizeMath(src))); };

  /* =======================================================================
     2. 滚动跟随 + 回到最新
     ======================================================================= */
  var stick   = true;      // 用户是否贴着底部
  var jumping = false;     // 平滑滚动进行中，忽略 scroll 事件

  function atBottom() {
    return window.innerHeight + window.scrollY
           >= document.documentElement.scrollHeight - 60;
  }

  function syncJumpBtn() {
    jumpBtn.classList.toggle("show", !stick);
  }

  function scrollToBottom(smooth) {
    if (smooth) {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    } else {
      window.scrollTo(0, document.body.scrollHeight);
    }
  }

  window.addEventListener("scroll", function () {
    if (jumping) return;
    stick = atBottom();
    syncJumpBtn();
  }, { passive: true });

  jumpBtn.addEventListener("click", function () {
    jumping = true;
    stick = true;
    syncJumpBtn();
    scrollToBottom(true);
    setTimeout(function () { jumping = false; }, 400);
  });

  /* =======================================================================
     3. 整体渲染（历史消息）
     ======================================================================= */
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // 只放行 http(s) 链接：搜索结果/来源 URL 属于不可信内容，
  // javascript:、data: 之类的协议会变成可点击的 XSS 入口（本页能调本地 API）。
  function safeUrl(url) {
    var u = String(url || "").trim();
    return /^https?:\/\//i.test(u) ? u : "#";
  }

  function buildSourcesHtml(list) {
    var items = "";
    (list || []).forEach(function (s) {
      var url   = String(s.url || "");
      var title = String(s.title || url);
      var date = String(s.date || "");
      var badge = date ? '<span class="src-date">' + escapeHtml(date) + '</span>' : '';
      items += '<li>' + badge + '<a href="' + escapeHtml(safeUrl(url)) + '" rel="noopener">'
             + escapeHtml(title) + '</a></li>';
    });
    return '<details class="sources"><summary>联网搜索 · ' + (list || []).length + ' 个来源</summary>'
         + '<ol>' + items + '</ol></details>';
  }

  // 把正文里的 [1][2] 变成可点击的来源链接（跳过代码块）
  function linkCitations(html, sources) {
    if (!sources || !sources.length) return html;
    var parts = html.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/);
    for (var i = 0; i < parts.length; i += 2) {
      parts[i] = parts[i].replace(/\[(\d{1,2})\]/g, function (m, n) {
        var idx = parseInt(n, 10) - 1;
        if (idx < 0 || idx >= sources.length) return m;
        return '<a class="cite" href="' + escapeHtml(safeUrl(sources[idx].url))
             + '" title="' + escapeHtml(sources[idx].title || "")
             + '">' + m + '</a>';
      });
    }
    return parts.join("");
  }

  // 聊天里不显示图表的 JSON，只给一条提示（导出文档时才真正渲染）
  function hideChartBlocks(html) {
    return html.replace(/<pre><code class="language-chart">([\s\S]*?)<\/code><\/pre>/g,
      function (m, body) {
        var title = "";
        try {
          var spec = JSON.parse(body.replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                                .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
          title = spec && spec.title ? spec.title : "";
        } catch (e) { /* 解析失败就不显示标题 */ }
        return '<div class="chart-note">📊 图表' + (title ? "：" + escapeHtml(title) : "")
             + '<span>（导出 DOCX / PDF 时渲染）</span></div>';
      });
  }

  // token 用量：只显示总消耗，输入/输出明细放进 title 悬停可见
  function buildUsageHtml(usage) {
    if (!usage || !usage.total_tokens) return "";
    var total = Number(usage.total_tokens) || 0;
    var pt = Number(usage.prompt_tokens) || 0;
    var ct = Number(usage.completion_tokens) || 0;
    var tip = "输入 " + pt.toLocaleString() + " · 输出 " + ct.toLocaleString();
    return '<div class="turn-usage" title="' + tip + '">本次消耗 '
         + total.toLocaleString() + ' tokens</div>';
  }

  function buildAttachmentsHtml(list) {
    var chips = (list || []).map(function (a) {
      var icon = a.kind === "image" ? "🖼" : "📄";
      return '<a class="attach" href="' + escapeHtml(a.url) + '"'
           + ' title="点击用系统默认程序打开">' + icon + ' '
           + escapeHtml(a.name) + '</a>';
    });
    return chips.length ? '<div class="attachments">' + chips.join("") + '</div>' : "";
  }

  // JS 无法直接调用 Python：把动作写进 document.title，由 QWebEngineView 的
  // titleChanged 信号接住（比自定义 URL scheme 更稳，不会被导航策略拦掉）
  function sendAction(name, index, extra) {
    document.title = "dsh:" + name + ":" + index + ":" + (extra || "") + ":" + Date.now();
  }

  function bindTurnActions() {
    chat.addEventListener("click", function (e) {
      var turn = e.target.closest(".turn");
      if (!turn) return;
      var index = turn.getAttribute("data-index");

      var verBtn = e.target.closest(".turn-ver");
      if (verBtn) {
        if (!verBtn.disabled) sendAction("version", index, verBtn.getAttribute("data-v"));
        return;
      }

      var delBtn = e.target.closest(".turn-del");
      if (delBtn) { sendAction("delete", index); return; }

      var copyBtn = e.target.closest(".turn-copy");
      if (copyBtn) { sendAction("copy", index); return; }

      var regenBtn = e.target.closest(".turn-regen");
      if (regenBtn) { sendAction("regenerate", index); return; }

      var editBtn = e.target.closest(".turn-edit");
      if (editBtn) { sendAction("edit", index); return; }

      var save = e.target.closest(".img-save");
      if (save) {
        var img = save.parentNode.querySelector("img");
        if (img) {
          sendAction("saveimage", index,
                     encodeURIComponent(img.getAttribute("src") || ""));
        }
        return;
      }

      var tool = e.target.closest(".turn-tool");
      if (tool) { sendAction("export", index, tool.getAttribute("data-fmt")); }
    });
  }

  // 助手回答下方的操作条：复制 / 重新生成（仅末条）/ 导出
  function buildExportTools(isLast) {
    var fmts = [["md", "MD"], ["docx", "Word"], ["pdf", "PDF"], ["xlsx", "Excel"]];
    var btns = '<button class="turn-tool turn-copy">复制</button>';
    if (isLast) btns += '<button class="turn-tool turn-regen">重新生成</button>';
    btns += fmts.map(function (f) {
      return '<button class="turn-tool" data-fmt="' + f[0] + '">' + f[1] + '</button>';
    }).join("");
    return '<div class="turn-tools">' + btns + '</div>';
  }

  // 用户消息下方的「编辑」按钮
  function buildUserTools() {
    return '<div class="turn-tools"><button class="turn-tool turn-edit">编辑</button></div>';
  }

  // 重新生成的多版本对比：‹ n/m › 切换器（与网页端 / 官方客户端同款）
  function buildVersionSwitcher(t) {
    var versions = t.versions || [];
    if (versions.length < 2) return "";
    var cur = t.version || 0;
    return '<div class="turn-versions">'
         + '<button class="turn-ver" data-v="' + (cur - 1) + '"'
         + (cur <= 0 ? " disabled" : "") + ' title="上一个版本">‹</button>'
         + '<span class="turn-ver-label">' + (cur + 1) + '/' + versions.length + '</span>'
         + '<button class="turn-ver" data-v="' + (cur + 1) + '"'
         + (cur >= versions.length - 1 ? " disabled" : "") + ' title="下一个版本">›</button>'
         + '</div>';
  }

  // 给正文里的图片加一个「保存图片」按钮
  function decorateImages(root) {
    var scope = root || document;
    Array.prototype.forEach.call(
      scope.querySelectorAll(".body img:not(.img-decorated)"), function (img) {
        img.classList.add("img-decorated");
        var wrap = document.createElement("span");
        wrap.className = "img-wrap";
        img.parentNode.insertBefore(wrap, img);
        wrap.appendChild(img);

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "img-save";
        btn.textContent = "保存图片";
        btn.title = "另存为…";
        wrap.appendChild(btn);
      });
  }

  function renderChat(turns, pending) {
    if ((!turns || turns.length === 0) && !pending) {
      chat.innerHTML = '<p class="pending">还没有对话，在下方输入内容开始吧。</p>';
      stick = true;
      syncJumpBtn();
      return;
    }

    var html = "";
    var lastAi = -1;
    (turns || []).forEach(function (t, i) { if (t.role !== "user") lastAi = i; });
    (turns || []).forEach(function (t, idx) {
      var cls  = t.role === "user" ? "turn user" : "turn ai";
      // 助手抬头显示用户配置的昵称/头像（人格），没配置才回退「模型」
      var roleHtml;
      if (t.role === "user") {
        roleHtml = '<div class="role">你</div>';
      } else {
        var av = t.persona_avatar || "";
        var avHtml = "";
        if (av && av.indexOf("data:") !== 0) avHtml = '<span class="role-avatar-emoji">' + escapeHtml(av) + '</span>';
        roleHtml = '<div class="role">' + avHtml + escapeHtml(t.persona || "模型") + '</div>';
      }

      var think = "";
      if (t.reasoning && t.reasoning.trim()) {
        think = '<details class="think"><summary>思考过程</summary>'
              + '<div class="think-body">' + marked.parse(t.reasoning) + '</div></details>';
      }

      var sources = (t.sources && t.sources.length) ? buildSourcesHtml(t.sources) : "";
      var atts = buildAttachmentsHtml(t.attachments);

      var bodyHtml = marked.parse(t.content);
      if (t.role !== "user") {
        bodyHtml = hideChartBlocks(bodyHtml);
        bodyHtml = linkCitations(bodyHtml, t.sources);
      }

      var tools = t.role === "user" ? buildUserTools() : buildExportTools(idx === lastAi);

      html += '<div class="' + cls + '" data-index="' + idx + '">'
            + '<button class="turn-del" title="删除这条对话">×</button>'
            + roleHtml
            + think
            + '<div class="body">' + bodyHtml + '</div>'
            + atts
            + (t.role !== "user" ? buildVersionSwitcher(t) : "")
            + tools
            + sources
            + (t.role !== "user" ? buildUsageHtml(t.usage) : "")
            + '</div>';
    });

    if (pending) {
      html += '<div class="turn ai"><div class="role">模型</div>'
            + '<div class="body pending">正在思考…</div></div>';
    }

    chat.innerHTML = html;
    decorateImages(chat);
    renderMath(chat);
    if (stick) scrollToBottom(false);
  }

  /* =======================================================================
     4. 流式渲染
     ======================================================================= */
  var stream      = null;   // 当前正在写的消息
  var streamTimer = null;   // 节流定时器

  function ensureTurn() {
    if (stream) return stream;

    var wrap = document.createElement("div");
    wrap.className = "turn ai";
    wrap.innerHTML = '<div class="role">模型</div>'
      + '<details class="think" open hidden><summary>思考过程</summary>'
      + '<div class="think-body"></div></details>'
      + '<div class="body"></div><div class="sources-box"></div><span class="cursor">▍</span>';
    chat.appendChild(wrap);

    stream = {
      root:       wrap,
      details:    wrap.querySelector("details.think"),
      think:      wrap.querySelector(".think-body"),
      body:       wrap.querySelector(".body"),
      sourcesBox: wrap.querySelector(".sources-box"),
      cursor:     wrap.querySelector(".cursor"),
      thinkText:  "",
      bodyText:   ""
    };
    return stream;
  }

  function flushStream() {
    streamTimer = null;
    if (!stream) return;

    var keep = stick;       // 更新 DOM 前先记住用户是否在底部

    if (stream.thinkText) {
      stream.details.hidden = false;
      stream.think.innerHTML = marked.parse(stream.thinkText);
    }
    stream.body.innerHTML = marked.parse(stream.bodyText);
    decorateImages(stream.body);

    if (keep) scrollToBottom(false);
  }

  function beginStream() {
    ensureTurn();
    if (stick) scrollToBottom(false);
  }

  function streamAppend(kind, text) {
    ensureTurn();
    if (kind === "think") {
      stream.thinkText += text;
    } else {
      stream.bodyText += text;
      if (!stream.details.hidden) stream.details.open = false;   // 正文开始，收起思考
    }
    if (!streamTimer) streamTimer = setTimeout(flushStream, 120);  // 节流
  }

  // 搜索结果一到就插进来，回答还在流式时就能点开看
  function streamSources(list) {
    if (!list || !list.length) return;
    var s = ensureTurn();
    if (!s.sourcesBox) return;
    s.sourcesBox.innerHTML = buildSourcesHtml(list);
    if (stick) scrollToBottom(false);
  }

  // 模型决定要先搜索时，把刚才已经流出去的正文丢掉
  function resetStream() {
    if (streamTimer) { clearTimeout(streamTimer); streamTimer = null; }
    if (stream && stream.root && stream.root.parentNode) {
      stream.root.parentNode.removeChild(stream.root);
    }
    stream = null;
  }

  function endStream() {
    if (streamTimer) { clearTimeout(streamTimer); streamTimer = null; }
    if (!stream) return;

    var keep = stick;
    flushStream();
    if (stream.bodyText)  renderMath(stream.body);
    if (stream.thinkText) renderMath(stream.think);
    stream.cursor.remove();
    stream = null;
    if (keep) scrollToBottom(false);
  }

  bindTurnActions();

  // Esc 停止生成（与网页端一致的快捷键）
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") sendAction("stop", 0);
  });

  /* =======================================================================
     5. 暴露给 Python 调用
     ======================================================================= */
  window.renderChat    = renderChat;
  window.beginStream   = beginStream;
  window.streamAppend  = streamAppend;
  window.streamSources = streamSources;
  window.resetStream   = resetStream;
  window.endStream     = endStream;
})();
