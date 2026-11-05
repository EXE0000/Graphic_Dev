/* Web 版聊天页：通过 SSE 和本地服务通信 */
(function () {
  "use strict";

  var token = new URLSearchParams(location.search).get("t") || "";
  var chat = document.getElementById("chat");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var meta = document.getElementById("meta");
  var resetBtn = document.getElementById("reset");
  var fileInput = document.getElementById("file");
  var pendingBox = document.getElementById("pending");
  var exportFormats = [["md", "MD"], ["docx", "Word"], ["pdf", "PDF"], ["xlsx", "Excel"]];
  var toggleSearch = document.getElementById("toggle-search");
  var reasoningRange = document.getElementById("reasoning-range");
  var reasoningLabel = document.getElementById("reasoning-label");
  var REASONING_LEVELS = ["off", "low", "medium", "high"];
  var REASONING_TEXT = { off: "关闭", low: "低", medium: "中", high: "高" };
  var manageBtn = document.getElementById("manage");
  var deleteSelectedBtn = document.getElementById("delete-selected");
  var cancelManageBtn = document.getElementById("cancel-manage");
  var managing = false;
  var jumpLatest = document.getElementById("jump-latest-web");
  var stick = true;        // 是否跟随最新内容
  var currentConv = "";    // 当前会话 id
  var conversations = [];  // 会话列表
  var convList = document.getElementById("conv-list");
  var convSearch = document.getElementById("conv-search");
  var convSearchClear = document.getElementById("conv-search-clear");
  var newConvBtn = document.getElementById("new-conv");
  var toggleSidebar = document.getElementById("toggle-sidebar");
  var searchTimer = null;
  var lastSearch = "";
  var convManaging = false;
  var convSelected = {};          // id -> true
  var convManageBtn = document.getElementById("conv-manage");
  var convDeleteBtn = document.getElementById("conv-delete-selected");
  var convDeleteEmptyBtn = document.getElementById("conv-delete-empty");
  var convCancelBtn = document.getElementById("conv-cancel-manage");
  var pendingItems = [];      // 当前待发送附件
  var streamPoll = null;      // 轮询"仍在生成"的会话
  var remoteStreaming = false; // 服务端仍在生成（刷新后也要显示「停止」）
  var serverBusy = false;      // 任意会话正在生成（别的会话里要禁用发送）
  var busyTimer = null;
  var exportAllBtn = document.getElementById("export-all");
  var exportFmt = document.getElementById("export-fmt");
  var busy = false;
  var currentController = null;
  var stateIndex = -1;   // 服务端 history 中本条回答的下标

  function api(path) {
    var url = path + (path.indexOf("?") >= 0 ? "&" : "?")
            + "t=" + encodeURIComponent(token);
    if (currentConv && path.indexOf("conv=") < 0) {
      url += "&conv=" + encodeURIComponent(currentConv);
    }
    return url;
  }

  // ------------------------------------------------------------ 会话列表
  function renderConversations(items) {
    conversations = items || [];
    var keyword = (convSearch.value || "").trim().toLowerCase();
    convList.innerHTML = "";

    conversations.forEach(function (conv) {
      if (keyword && conv.title.toLowerCase().indexOf(keyword) < 0) return;

      var row = el("div", "conv-item" + (conv.id === currentConv ? " active" : "")
                            + (convSelected[conv.id] ? " selected" : ""));
      if (convManaging) {
        row.classList.add("managing");
        var box = el("input", "conv-check");
        box.type = "checkbox";
        box.checked = !!convSelected[conv.id];
        box.addEventListener("click", function (event) {
          event.stopPropagation();
          toggleConvSelect(conv.id);
        });
        row.appendChild(box);
      }
      row.appendChild(document.createTextNode(conv.title || "新对话"));
      row.title = conv.title || "新对话";
      row.addEventListener("click", function () {
        if (convManaging) { toggleConvSelect(conv.id); return; }
        switchConversation(conv.id);
      });

      var actions = el("span", "conv-actions");

      var rename = el("button", null, "✎");
      rename.title = "重命名";
      rename.addEventListener("click", function (event) {
        event.stopPropagation();
        renameConversation(conv);
      });
      actions.appendChild(rename);

      var remove = el("button", null, "🗑");
      remove.title = "删除对话";
      remove.addEventListener("click", function (event) {
        event.stopPropagation();
        deleteConversation(conv);
      });
      actions.appendChild(remove);

      row.appendChild(actions);
      convList.appendChild(row);
    });
  }

  function toggleConvSelect(cid) {
    if (convSelected[cid]) delete convSelected[cid];
    else convSelected[cid] = true;
    renderConversations(conversations);
    refreshConvBar();
  }

  function refreshConvBar() {
    var count = Object.keys(convSelected).length;
    convDeleteBtn.textContent = "删除选中 (" + count + ")";
    convDeleteBtn.disabled = count === 0;
  }

  function setConvManaging(on) {
    convManaging = on;
    convSelected = {};
    convManageBtn.hidden = on;
    convDeleteBtn.hidden = !on;
    convDeleteEmptyBtn.hidden = !on;
    convCancelBtn.hidden = !on;
    renderConversations(conversations);
    refreshConvBar();
  }

  convManageBtn.addEventListener("click", function () { setConvManaging(true); });
  convCancelBtn.addEventListener("click", function () { setConvManaging(false); });

  convDeleteBtn.addEventListener("click", function () {
    var ids = Object.keys(convSelected);
    if (!ids.length) return;
    if (!confirm("确定删除选中的 " + ids.length + " 个对话吗？此操作不可恢复。")) return;
    postJSON("/api/conversations/delete_batch", { ids: ids })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        if (ids.indexOf(currentConv) >= 0) {
          currentConv = data.active || (items[0] && items[0].id) || "";
          loadHistory();
        }
        setConvManaging(false);
        renderConversations(items);
      })
      .catch(function () {});
  });

  convDeleteEmptyBtn.addEventListener("click", function () {
    if (!confirm("删除所有没有消息的对话？")) return;
    postJSON("/api/conversations/delete_empty", {})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        currentConv = data.active || (items[0] && items[0].id) || "";
        setConvManaging(false);
        renderConversations(items);
        loadHistory();
        meta.textContent = "已删除 " + (data.removed || 0) + " 个空对话";
      })
      .catch(function () {});
  });

  function loadConversations(keepActive) {
    fetch(api("/api/conversations"))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!keepActive || !currentConv) currentConv = data.active || "";
        serverBusy = !!data.busy;
        updateSendState();
        if (serverBusy) pollBusy();
        renderConversations(data.items || []);
      })
      .catch(function () {});
  }

  function switchConversation(cid, focusIndex) {
    // 切走时如果原会话还在生成：断开本地连接（服务端会继续跑完），
    // 同时标记"服务端忙"，新会话里按钮变成禁止态。
    if (busy) {
      try { if (currentController) currentController.abort(); } catch (e) { /* 忽略 */ }
      busy = false;
      currentController = null;
      serverBusy = true;
      pollBusy();
    } else if (remoteStreaming) {
      serverBusy = true;
      pollBusy();
    }
    stopStreamPoll();
    var same = (cid === currentConv);
    currentConv = cid;
    if (!same) loadConversations(true);
    loadHistory(focusIndex);
  }

  function renameConversation(conv) {
    var title = prompt("重命名对话", conv.title || "新对话");
    if (title === null) return;
    title = title.trim();
    if (!title) return;
    postJSON("/api/conversations/rename", { id: conv.id, title: title })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderConversations(data.items || []);
        if (conv.id === currentConv) setTitle(title);
      })
      .catch(function () {});
  }

  function deleteConversation(conv) {
    if (!confirm("确定删除对话「" + (conv.title || "新对话") + "」吗？此操作不可恢复。")) return;
    postJSON("/api/conversations/delete", { id: conv.id })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        if (conv.id === currentConv) {
          currentConv = data.active || (items[0] && items[0].id) || "";
          loadHistory();
        }
        renderConversations(items);
      })
      .catch(function () {});
  }

  function setTitle(text) {
    document.title = (text ? text + " · " : "") + "AI Chat";
  }

  // 收起 / 展开侧边栏
  var layout = document.querySelector(".layout");
  if (localStorage.getItem("sidebar-collapsed") === "1") {
    layout.classList.add("collapsed");
  }
  if (toggleSidebar) toggleSidebar.addEventListener("click", function () {
    layout.classList.toggle("collapsed");
    localStorage.setItem("sidebar-collapsed",
      layout.classList.contains("collapsed") ? "1" : "0");
  });

  newConvBtn.addEventListener("click", function () {
    postJSON("/api/conversations/new", {})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        currentConv = data.id;
        renderConversations(data.items || []);
        chat.innerHTML = "";
        stateIndex = -1;
        renderPending([]);
        setTitle("新对话");
        input.focus();
      })
      .catch(function () {});
  });

  function syncSearchClear() {
    convSearchClear.hidden = !convSearch.value;
  }

  convSearchClear.addEventListener("click", function () {
    convSearch.value = "";
    syncSearchClear();
    renderConversations(conversations);
    convSearch.focus();
  });

  convSearch.addEventListener("input", function () {
    syncSearchClear();
    var keyword = convSearch.value.trim();
    renderConversations(conversations);      // 先按标题过滤，响应快

    if (searchTimer) clearTimeout(searchTimer);
    if (keyword.length < 2) {
      lastSearch = "";
      if (keyword.length === 0) renderConversations(conversations);
      return;
    }
    searchTimer = setTimeout(function () { searchAll(keyword); }, 250);
  });

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function highlight(text, keyword) {
    var safe = escapeHtml(text);
    if (!keyword) return safe;
    var escaped = escapeHtml(keyword).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return safe.replace(new RegExp("(" + escaped + ")", "gi"), "<mark>$1</mark>");
  }

  function searchAll(keyword) {
    lastSearch = keyword;
    fetch(api("/api/search?q=" + encodeURIComponent(keyword)))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (keyword !== convSearch.value.trim()) return;   // 结果过期
        renderSearchHits(data.items || [], keyword);
      })
      .catch(function () {});
  }

  function renderSearchHits(items, keyword) {
    convList.innerHTML = "";
    if (!items.length) {
      var empty = el("div", "conv-empty", "没有匹配的对话");
      convList.appendChild(empty);
      return;
    }
    items.forEach(function (hit) {
      var row = el("div", "conv-hit");
      if (hit.id === currentConv) row.style.background = "#e8f1ff";

      var title = el("div", "hit-title");
      title.innerHTML = highlight(hit.title, hit.title_hit ? keyword : "");
      row.appendChild(title);

      if (hit.match) {
        var snippet = el("div", "hit-snippet");
        snippet.innerHTML = highlight(hit.match.snippet, keyword);
        row.appendChild(snippet);
      }

      row.addEventListener("click", function () {
        switchConversation(hit.id, hit.match ? hit.match.index : null);
      });
      convList.appendChild(row);
    });
  }

  // ------------------------------------------------------------ 滚动
  function nearBottom() {
    var box = chat;
    if (box.scrollHeight <= box.clientHeight + 1) {
      box = document.scrollingElement || document.documentElement;
    }
    return box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }

  function focusInput() {
    try {
      input.focus({ preventScroll: true });   // 聚焦不要把整页滚下去
    } catch (e) {
      input.focus();
    }
  }

  function keepPageTop() {
    if (window.scrollY !== 0) window.scrollTo(0, 0);
  }

  function scrollToBottom(force) {
    if (force) stick = true;
    var box = chat;
    if (box.scrollHeight <= box.clientHeight + 1) {
      box = document.scrollingElement || document.documentElement;
    }
    box.scrollTop = box.scrollHeight;
    jumpLatest.hidden = true;
  }

  function updateJump() {
    stick = nearBottom();
    jumpLatest.hidden = stick;
  }

  // 用捕获阶段监听整个文档：不管实际滚动的是 #chat 还是 body，都能收到
  document.addEventListener("scroll", updateJump, true);
  window.addEventListener("scroll", updateJump, true);
  window.addEventListener("resize", updateJump);
  setInterval(updateJump, 800);
  setInterval(keepPageTop, 1200);        // 兜底：即使漏了事件也能显示

  jumpLatest.addEventListener("click", function () { scrollToBottom(true); });

  // ------------------------------------------------------------ 渲染
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // 给正文图片加「保存图片」按钮
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
        btn.addEventListener("click", function () { saveImage(img.src); });
        wrap.appendChild(btn);
      });
  }

  function saveImage(src) {
    fetch(src)
      .then(function (r) { return r.blob(); })
      .then(function (blob) {
        var name = src.split("?")[0].split("/").pop() || "image.png";
        if (name.indexOf(".") < 0) name += ".png";
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
      })
      .catch(function () { alert("保存失败：图片可能已被删除"); });
  }

  // 把正文里的 [1][2] 变成可点击的来源链接（跳过代码块和已有链接）
  function linkCitations(root, sources) {
    if (!root || !sources || !sources.length) return;

    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || node.nodeValue.indexOf("[") < 0) {
          return NodeFilter.FILTER_REJECT;
        }
        var parent = node.parentNode;
        while (parent && parent !== root) {
          if (parent.tagName === "A" || parent.tagName === "CODE"
              || parent.tagName === "PRE") {
            return NodeFilter.FILTER_REJECT;
          }
          parent = parent.parentNode;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });

    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(function (node) {
      var html = node.nodeValue.replace(/\[(\d{1,2})\]/g, function (match, num) {
        var index = parseInt(num, 10) - 1;
        var source = sources[index];
        if (!source || !source.url) return match;
        var title = (source.title || source.url || "").replace(/"/g, "&quot;");
        return '<a class="cite" href="' + source.url + '" target="_blank"'
             + ' rel="noopener noreferrer" title="' + title + '">'
             + match + "</a>";
      });
      if (html === node.nodeValue) return;

      var span = document.createElement("span");
      span.innerHTML = html;
      node.parentNode.replaceChild(span, node);
    });
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

  function renderMarkdown(text) {
    var html = window.marked ? marked.parse(text)
                             : String(text).replace(/</g, "&lt;");
    return sanitizeHtml(html);
  }

  function paintBody(node, markdown, sources) {
    if (!node) return;
    node.innerHTML = renderMarkdown(markdown || "");
    typeset(node);
    decorateImages(node);
    linkCitations(node, sources);
  }

  function typeset(node) {
    if (!window.renderMathInElement) return;
    try {
      renderMathInElement(node, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\[", right: "\\]", display: true },
          { left: "\\( ", right: " \\)", display: false },
          { left: "\\(", right: "\\)", display: false }
        ],
        throwOnError: false,
        ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
      });
    } catch (e) { /* 公式出错不影响正文 */ }
  }

  function addTurn(role, index) {
    var turn = el("div", "turn " + (role === "user" ? "user" : "ai"));
    if (index != null) turn.dataset.index = index;

    var check = el("input", "turn-check");
    check.type = "checkbox";
    check.addEventListener("change", refreshSelectedCount);
    turn.appendChild(check);

    var del = el("button", "turn-del", "×");
    del.title = "删除这条对话";
    del.addEventListener("click", function () {
      deleteTurns([Number(turn.dataset.index)]);
    });
    turn.appendChild(del);

    turn.appendChild(el("div", "role", role === "user" ? "你" : "模型"));
    var body = el("div", "body");
    turn.appendChild(body);
    chat.appendChild(turn);
    if (stick) chat.scrollTop = chat.scrollHeight;
    return { turn: turn, body: body };
  }

  function addThink(turn, open) {
    var details = el("details", "think");
    details.open = (open !== false);      // 默认展开；恢复历史时传 false 折叠起来
    details.appendChild(el("summary", null, "思考过程"));
    var body = el("div", "think-body");
    details.appendChild(body);
    turn.appendChild(details);
    return { details: details, body: body };
  }

  // ------------------------------------------------------------ 配置
  function loadConfig(retries) {
    retries = retries || 0;
    fetch(api("/api/config"))
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      var bits = [cfg.model || "未选择模型"];
      if (cfg.search) bits.push("联网搜索");
      if (cfg.image) bits.push("文生图");
      if (cfg.reasoning_supported !== false && cfg.reasoning
          && cfg.reasoning !== "off") {
        bits.push("推理" + (REASONING_TEXT[cfg.reasoning] || ""));
      }
      meta.textContent = bits.join(" · ");
      if (!cfg.ready) meta.textContent = "配置不完整：" + (cfg.reason || "");
      toggleSearch.checked = !!cfg.search;
      if (cfg.reasoning !== undefined) {
        var index = REASONING_LEVELS.indexOf(cfg.reasoning);
        if (index < 0) index = 2;
        reasoningRange.value = index;
        reasoningLabel.textContent = REASONING_TEXT[REASONING_LEVELS[index]];
      }

      // 能力探测是异步的：还没出结果时，稍后重拉配置
      if (cfg.reasoning_probe === "pending" && retries < 3) {
        setTimeout(function () { loadConfig(retries + 1); }, 3000);
      }

      // 模型不支持推理强度 → 滑块置灰
      var supported = cfg.reasoning_supported !== false;
      reasoningRange.disabled = !supported;
      reasoningRange.closest(".reasoning").classList.toggle("disabled", !supported);
      reasoningRange.title = supported
        ? (cfg.reasoning_note || "推理强度：越高越慢但更仔细")
        : (cfg.reasoning_note || "该模型不支持调整推理强度");
    })
    .catch(function () { meta.textContent = "配置读取失败"; });
  }

  loadConfig(0);

  // ------------------------------------------------------------ 附件
  function renderPending(items) {
    pendingItems = items || [];
    pendingBox.innerHTML = "";
    (items || []).forEach(function (item) {
      var chip = el("span", "chip");
      chip.appendChild(document.createTextNode(
        (item.kind === "image" ? "🖼 " : "📄 ") + item.name));
      var del = el("button", null, "✕");
      del.title = "移除";
      del.addEventListener("click", function () {
        postJSON("/api/attachments/remove", { id: item.id })
          .then(function (r) { return r.json(); })
          .then(function (data) { renderPending(data.items); })
          .catch(function () {});
      });
      chip.appendChild(del);
      pendingBox.appendChild(chip);
    });
  }

  function postJSON(path, body) {
    return fetch(api(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }

  function uploadOne(file) {
    return new Promise(function (resolve) {
      var reader = new FileReader();
      reader.onload = function () {
        var bytes = new Uint8Array(reader.result);
        var binary = "";
        for (var i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
        }
        postJSON("/api/upload", { name: file.name, data: btoa(binary) })
          .then(function (r) {
            if (!r.ok) {
              return r.text().then(function (t) { alert(file.name + "：" + t); });
            }
            return r.json().then(function (data) { renderPending(data.items); });
          })
          .then(resolve, resolve);
      };
      reader.onerror = function () { resolve(); };
      reader.readAsArrayBuffer(file);
    });
  }

  fileInput.addEventListener("change", function () {
    var files = Array.prototype.slice.call(fileInput.files || []);
    fileInput.value = "";
    files.reduce(function (chain, file) {
      return chain.then(function () { return uploadOne(file); });
    }, Promise.resolve());
  });

  // ------------------------------------------------------------ 导出
  function addTools(turn, index) {
    var bar = el("div", "turn-tools");
    exportFormats.forEach(function (pair) {
      var btn = el("button", "turn-tool", pair[1]);
      btn.addEventListener("click", function () {
        btn.disabled = true;
        postJSON("/api/export", { index: index, fmt: pair[0] })
          .then(function (resp) {
            if (!resp.ok) {
              return resp.text().then(function (t) { alert("导出失败：" + t); });
            }
            var disposition = resp.headers.get("Content-Disposition") || "";
            var match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
            var name = match ? decodeURIComponent(match[1])
                             : "answer." + pair[0];
            return resp.blob().then(function (blob) {
              var link = document.createElement("a");
              link.href = URL.createObjectURL(blob);
              link.download = name;
              document.body.appendChild(link);
              link.click();
              link.remove();
              setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
            });
          })
          .catch(function (e) { alert("导出失败：" + e); })
          .then(function () { btn.disabled = false; });
      });
      bar.appendChild(btn);
    });
    turn.appendChild(bar);
  }

  // ------------------------------------------------------------ 删除
  function deleteTurns(indexes) {
    indexes = indexes.filter(function (i) { return !isNaN(i); });
    if (!indexes.length) return;
    var label = indexes.length > 1 ? ("这 " + indexes.length + " 条对话") : "这条对话";
    if (!confirm("确定删除" + label + "吗？（问答会一起删掉）")) return;

    postJSON("/api/delete_batch", { indexes: indexes })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderHistory(data.turns || []);
        meta.textContent = "已删除";
        loadConversations(true);
      })
      .catch(function () {});
  }

  function refreshSelectedCount() {
    var count = chat.querySelectorAll(".turn-check:checked").length;
    deleteSelectedBtn.textContent = "删除选中 (" + count + ")";
    deleteSelectedBtn.disabled = count === 0;
  }

  function setManaging(on) {
    managing = on;
    chat.classList.toggle("managing", on);
    manageBtn.hidden = on;
    deleteSelectedBtn.hidden = !on;
    cancelManageBtn.hidden = !on;
    if (!on) {
      chat.querySelectorAll(".turn-check").forEach(function (box) {
        box.checked = false;
      });
    }
    refreshSelectedCount();
  }

  exportAllBtn.addEventListener("click", function () {
    var fmt = exportFmt.value || "md";
    exportAllBtn.disabled = true;
    postJSON("/api/export_all", { fmt: fmt })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.text().then(function (t) { alert("导出失败：" + t); });
        }
        var disposition = resp.headers.get("Content-Disposition") || "";
        var match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
        var name = match ? decodeURIComponent(match[1]) : ("对话导出." + fmt);
        return resp.blob().then(function (blob) {
          var link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = name;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
        });
      })
      .catch(function (e) { alert("导出失败：" + e); })
      .then(function () { exportAllBtn.disabled = false; });
  });

  manageBtn.addEventListener("click", function () { setManaging(true); });
  cancelManageBtn.addEventListener("click", function () { setManaging(false); });
  deleteSelectedBtn.addEventListener("click", function () {
    var indexes = Array.prototype.map.call(
      chat.querySelectorAll(".turn-check:checked"),
      function (box) { return Number(box.closest(".turn").dataset.index); });
    deleteTurns(indexes);
    setManaging(false);
  });

  // ------------------------------------------------------------ 设置
  function pushSettings() {
    postJSON("/api/setting", {
      search: toggleSearch.checked,
      reasoning: REASONING_LEVELS[Number(reasoningRange.value)] || "medium"
    }).then(function (r) { return r.json(); }).then(function (cfg) {
      var bits = [cfg.model || "未选择模型"];
      if (cfg.search) bits.push("联网搜索");
      if (cfg.image) bits.push("文生图");
      if (cfg.reasoning_supported !== false && cfg.reasoning
          && cfg.reasoning !== "off") {
        bits.push("推理" + (REASONING_TEXT[cfg.reasoning] || ""));
      }
      meta.textContent = bits.join(" · ");
    }).catch(function () {});
  }

  toggleSearch.addEventListener("change", pushSettings);

  // 推理强度滑块：拖动时只更新文字，松手（change）才请求后端，避免刷屏
  reasoningRange.addEventListener("input", function () {
    var level = REASONING_LEVELS[Number(reasoningRange.value)] || "medium";
    reasoningLabel.textContent = REASONING_TEXT[level];
  });

  reasoningRange.addEventListener("change", function () {
    pushSettings();
  });

  // ------------------------------------------------------------ 历史恢复
  function renderHistoryTurn(turn) {
    var node = addTurn(turn.role, turn.index);
    if (turn.reasoning) {
      // 刷新恢复历史时默认折叠；只有正在生成的那条保持展开
      var think = addThink(node.turn, !!turn.streaming);
      think.body.innerHTML = renderMarkdown(turn.reasoning);
      typeset(think.body);
    }
    paintBody(node.body, turn.content || "", turn.sources || []);
    if (turn.streaming) {
      node.turn.classList.add("streaming");
      var badge = el("div", "streaming-badge", "生成中…");
      node.turn.appendChild(badge);
    }

    if (turn.role === "assistant") {
      var sources = renderSources({ sources: turn.sources || [] });
      if (sources) node.turn.appendChild(sources);
      if (turn.content) addTools(node.turn, turn.index);
    }
    return node;
  }

  function renderHistory(turns) {
    chat.innerHTML = "";
    (turns || []).forEach(renderHistoryTurn);
    stateIndex = turns && turns.length ? turns[turns.length - 1].index : -1;
    refreshSelectedCount();
    scrollToBottom(true);
    keepPageTop();
  }

  function updateSendState() {
    sendBtn.classList.remove("stopping", "blocked");

    if (busy) {                       // 本页正在接收流
      sendBtn.textContent = "停止";
      sendBtn.classList.add("stopping");
      sendBtn.disabled = false;
      sendBtn.title = "";
      return;
    }
    if (remoteStreaming) {            // 刷新后，当前会话仍在生成
      sendBtn.textContent = "停止";
      sendBtn.classList.add("stopping");
      sendBtn.disabled = false;
      sendBtn.title = "";
      return;
    }
    if (serverBusy) {                 // 别的会话正在生成 → 禁止发送
      sendBtn.textContent = "🚫";
      sendBtn.classList.add("blocked");
      sendBtn.disabled = true;
      sendBtn.title = "另一个对话正在生成，完成后再发送";
      return;
    }
    sendBtn.textContent = "发送";
    sendBtn.disabled = false;
    sendBtn.title = "";
  }

  function pollBusy() {
    if (busyTimer) return;
    busyTimer = setInterval(function () {
      fetch(api("/api/conversations"))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          serverBusy = !!data.busy;
          updateSendState();
          if (!serverBusy) { clearInterval(busyTimer); busyTimer = null; }
        })
        .catch(function () {});
    }, 2000);
  }

  function setRemoteStreaming(on) {
    remoteStreaming = on;
    updateSendState();
  }

  function stopRemoteGeneration() {
    postJSON("/api/stop", {}).catch(function () {});
    meta.textContent = "正在停止…";
    sendBtn.textContent = "停止中…";
    pollBusy();
  }

  function stopStreamPoll() {
    if (streamPoll) { clearInterval(streamPoll); streamPoll = null; }
  }

  function startStreamPoll() {
    if (streamPoll) return;
    streamPoll = setInterval(refreshStreaming, 1500);
  }

  function refreshStreaming() {
    if (!currentConv) return;
    fetch(api("/api/history"))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.id && data.id !== currentConv) return;
        var turns = data.turns || [];
        var last = turns.length ? turns[turns.length - 1] : null;
        if (!last) { stopStreamPoll(); return; }

        var node = chat.querySelector('.turn[data-index="' + last.index + '"]');
        if (node) {
          var body = node.querySelector(".body");
          if (body && last.content) {
            paintBody(body, last.content, last.sources || []);
          }
          var thinkBody = node.querySelector(".think-body");
          if (thinkBody && last.reasoning) {
            thinkBody.innerHTML = renderMarkdown(last.reasoning);
            typeset(thinkBody);
            var details = node.querySelector("details.think");
            if (details) details.hidden = false;
          }
          if (stick) chat.scrollTop = chat.scrollHeight;
        }

        if (!last.streaming) {
          stopStreamPoll();
          setRemoteStreaming(false);
          if (node) {
            node.classList.remove("streaming");
            var badge = node.querySelector(".streaming-badge");
            if (badge) badge.remove();
          }
          loadConversations(true);
        }
      })
      .catch(function () {});
  }

  function loadHistory(focusIndex) {
    fetch(api("/api/history"))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.id) currentConv = data.id;
        renderHistory(data.turns || []);
        renderConversations(conversations);   // 刷新高亮，避免切换后高亮错位
        var first = (data.turns || [])[0];
        setTitle(first ? "对话" : "新对话");

        renderPending(data.attachments || []);      // 附件按会话保存

        var turns = data.turns || [];
        var lastTurn = turns.length ? turns[turns.length - 1] : null;
        if (lastTurn && lastTurn.streaming) {
          setRemoteStreaming(true);
          startStreamPoll();
        } else {
          setRemoteStreaming(false);
          stopStreamPoll();
        }

        if (focusIndex != null) {
          var target = chat.querySelector('.turn[data-index="' + focusIndex + '"]');
          if (target) {
            target.scrollIntoView({ block: "center" });
            target.style.transition = "background .3s";
            target.style.background = "#fff8d6";
            setTimeout(function () { target.style.background = ""; }, 1600);
          }
        }
      })
      .catch(function () {});
  }

  // ------------------------------------------------------------ 发送
  function handleEvent(block, state) {
    var lines = block.split("\n");
    var event = "message";
    var data = "";
    lines.forEach(function (line) {
      if (line.indexOf("event:") === 0) event = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
    });
    if (!data) return;

    var payload;
    try { payload = JSON.parse(data); } catch (e) { return; }

    if (event === "chunk") {
      state.text += payload.text || "";
      state.dirty = true;
    } else if (event === "think") {
      state.think += payload.text || "";
      state.thinkDirty = true;
    } else if (event === "note") {
      meta.textContent = payload.text || "";
    } else if (event === "sources") {
      state.sources = payload.items || [];
    } else if (event === "image") {
      state.text += "\n\n" + (payload.markdown || "") + "\n\n";
      state.dirty = true;
    } else if (event === "error") {
      state.text += "\n\n**出错了：** " + (payload.text || "");
      state.dirty = true;
    } else if (event === "done") {
      state.finished = true;
    }
  }

  function renderSources(state) {
    if (!state.sources || !state.sources.length) return null;
    var details = el("details", "sources");
    details.appendChild(el("summary", null,
      "联网搜索 · " + state.sources.length + " 个来源"));
    var list = el("ol");
    state.sources.forEach(function (s) {
      var li = el("li");
      if (s.date) {
        var badge = el("span", "src-date", s.date);
        li.appendChild(badge);
      }
      var link = el("a", null, s.title || s.url);
      link.href = s.url;
      link.target = "_blank";
      link.rel = "noopener";
      li.appendChild(link);
      list.appendChild(li);
    });
    details.appendChild(list);
    return details;
  }

  async function send() {
    if (busy) return;
    if (serverBusy) {
      meta.textContent = "另一个对话正在生成，完成后再发送";
      fetch(api("/api/conversations"))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          serverBusy = !!data.busy;
          updateSendState();
        })
        .catch(function () {});
      return;
    }
    var text = input.value.trim();
    if (!text && !pendingItems.length) return;   // 只有附件也可以发送

    input.value = "";
    input.style.height = "auto";
    busy = true;
    updateSendState();

    var user = addTurn("user");
    paintBody(user.body, text, []);

    var ai = addTurn("ai");
    var think = addThink(ai.turn);
    think.details.hidden = true;
    ai.body.classList.add("cursor-blink");

    var state = { text: "", think: "", sources: [], dirty: false,
                  thinkDirty: false, finished: false, stopped: false };
    var toolsAdded = false;

    var timer = setInterval(function () {
      if (state.thinkDirty) {
        state.thinkDirty = false;
        think.details.hidden = false;
        think.body.innerHTML = renderMarkdown(state.think);
        typeset(think.body);
      }
      if (state.dirty) {
        state.dirty = false;
        paintBody(ai.body, state.text, state.sources);
        if (stick) chat.scrollTop = chat.scrollHeight;
      }
    }, 120);

    try {
      currentController = new AbortController();
      var resp = await fetch(api("/api/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
        signal: currentController.signal
      });

      if (!resp.ok) {
        // 写进 state，交给统一的收尾渲染，避免被"没有返回内容"覆盖
        state.text = "**请求失败：** " + (await resp.text()).slice(0, 200);
        state.dirty = true;
      } else {
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        while (true) {
          var chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          var blocks = buffer.split("\n\n");
          buffer = blocks.pop();
          blocks.forEach(function (block) {
            if (block.trim()) handleEvent(block, state);
          });
        }
        if (buffer.trim()) handleEvent(buffer, state);
      }
    } catch (e) {
      if (e && e.name === "AbortError") {
        state.stopped = true;                 // 用户点了「停止」
      } else {
        state.text += "\n\n**连接中断：** " + e;
        state.dirty = true;
      }
    }

    clearInterval(timer);
    paintBody(ai.body, state.text || "（没有返回内容）", state.sources);
    ai.body.classList.remove("cursor-blink");

    var sources = renderSources(state);
    if (sources) ai.turn.appendChild(sources);

    // 用服务端历史校准索引；停止时服务端还要收尾，稍等一下再拉
    setTimeout(function () {
    fetch(api("/api/history"))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var turns = data.turns || [];
        var last = turns.length ? turns[turns.length - 1] : null;
        loadConversations(true);        // 首条消息会生成标题
        stateIndex = last ? last.index : -1;
        if (last && last.role === "assistant") {
          ai.turn.dataset.index = last.index;
          if (last.index > 0) user.turn.dataset.index = last.index - 1;
        }
        if (last && last.role === "assistant" && state.text && !toolsAdded) {
          toolsAdded = true;
          addTools(ai.turn, last.index);
        }
      })
      .catch(function () {});
    }, state.stopped ? 500 : 0);

    if (state.stopped) {
      ai.turn.appendChild(el("div", "stopped-note", "已停止生成"));
    }

    if (stick) chat.scrollTop = chat.scrollHeight;
    busy = false;
    currentController = null;
    keepPageTop();
    updateSendState();
    input.focus();
  }

  // ------------------------------------------------------------ 事件
  function stopGeneration() {
    if (!busy) return;
    try {
      if (currentController) currentController.abort();
    } catch (e) { /* 忽略 */ }
    postJSON("/api/stop", {}).catch(function () {});
    meta.textContent = "已停止生成";
  }

  sendBtn.addEventListener("click", function () {
    if (sendBtn.disabled) return;          // 别的会话正在生成，禁止发送
    if (busy) { stopGeneration(); return; }
    if (remoteStreaming) { stopRemoteGeneration(); return; }
    send();
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  });

  resetBtn.addEventListener("click", function () {
    fetch(api("/api/reset"), { method: "POST" }).catch(function () {});
    chat.innerHTML = "";
    stateIndex = -1;
    renderPending([]);
    loadConversations(true);
  });

  updateSendState();
  loadConversations(false);
  loadHistory();
  keepPageTop();
  focusInput();
})();
