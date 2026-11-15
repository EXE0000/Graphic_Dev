/* 知识库投喂台前端：基管理 / 文档投喂 / 手动知识点 / 检索试验 / 知识块编辑 */
(function () {
  "use strict";
  var TOKEN = new URLSearchParams(location.search).get("t") || "";
  var $ = function (id) { return document.getElementById(id); };

  var bases = [], activeId = "", cur = "";

  function api(path) {
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  }
  function getJSON(path) {
    return fetch(api(path)).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(t); });
      return r.json();
    });
  }
  function postJSON(path, body) {
    return fetch(api(path), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(t); });
      return r.json();
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2400);
  }

  // ---------------- 主题 ----------------
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("starty-theme", mode);
  }
  applyTheme(localStorage.getItem("starty-theme") || "dark");
  $("themeBtn").addEventListener("click", function () {
    var curMode = document.documentElement.getAttribute("data-theme");
    applyTheme(curMode === "dark" ? "light" : "dark");
  });
  $("backChat").href = "/chat?t=" + encodeURIComponent(TOKEN);

  // ---------------- 基列表 ----------------
  function loadBases(keepCur) {
    return getJSON("/api/kb/bases").then(function (d) {
      bases = d.items || []; activeId = d.active || "";
      if (!keepCur || !bases.some(function (b) { return b.id === cur; })) {
        cur = activeId || (bases[0] && bases[0].id) || "";
      }
      renderBases();
      return loadCurrent();
    }).catch(function (e) { toast("加载知识库失败：" + e.message); });
  }
  function renderBases() {
    var box = $("baseList");
    box.innerHTML = "";
    bases.forEach(function (b) {
      var card = document.createElement("div");
      card.className = "kb-base" + (b.id === cur ? " is-cur" : "");
      card.innerHTML =
        '<div class="kb-base-name">' + (b.id === activeId ? '<span class="dot"></span>' : "") +
        esc(b.name) + "</div>" +
        '<div class="kb-base-meta">' + b.docs + " 文档 · " + b.chunks + " 知识块</div>" +
        '<div class="kb-base-actions">' +
        '<button class="kb-mini" data-act="rename">重命名</button>' +
        '<button class="kb-mini danger" data-act="del">删除</button></div>';
      card.addEventListener("click", function (e) {
        var act = e.target.getAttribute && e.target.getAttribute("data-act");
        if (act === "rename") {
          var name = prompt("重命名知识库", b.name);
          if (name) postJSON("/api/kb/base/rename", { id: b.id, name: name })
            .then(function () { loadBases(true); });
          return;
        }
        if (act === "del") {
          if (confirm("删除知识库「" + b.name + "」及其全部文档？"))
            postJSON("/api/kb/base/delete", { id: b.id }).then(function () { loadBases(false); });
          return;
        }
        cur = b.id; renderBases(); loadCurrent();
      });
      box.appendChild(card);
    });
  }
  $("newBaseBtn").addEventListener("click", function () {
    var name = $("newBaseName").value.trim();
    if (!name) { toast("先给知识库起个名字"); return; }
    postJSON("/api/kb/base/create", { name: name, embed_model: $("newBaseEmbed").value.trim() })
      .then(function (d) {
        $("newBaseName").value = ""; $("newBaseEmbed").value = "";
        cur = d.id; toast("已创建「" + name + "」");
        loadBases(true);
      }).catch(function (e) { toast(e.message); });
  });

  // ---------------- 当前基面板 ----------------
  function loadCurrent() {
    var has = !!cur;
    $("emptyBase").hidden = has;
    $("basePanel").hidden = !has;
    if (!has) return Promise.resolve();
    var b = bases.filter(function (x) { return x.id === cur; })[0];
    $("curBaseName").textContent = b ? b.name : "";
    $("activeToggle").checked = (cur === activeId);
    return Promise.all([
      getJSON("/api/kb/stats?base=" + cur),
      getJSON("/api/kb/docs?base=" + cur)
    ]).then(function (res) {
      renderStats(res[0]);
      renderDocs(res[1].items || []);
      return loadChunks();
    }).catch(function (e) { toast(e.message); });
  }
  function renderStats(s) {
    $("curStats").innerHTML =
      "<span><b>" + s.docs + "</b> 文档</span><span><b>" + s.chunks + "</b> 知识块</span>" +
      "<span><b>" + s.vectors + "</b> 向量</span>";
  }
  $("activeToggle").addEventListener("change", function () {
    postJSON("/api/kb/base/active", { id: this.checked ? cur : "" })
      .then(function (d) { activeId = d.active; renderBases(); toast(activeId ? "已在对话中启用" : "已停用"); });
  });

  // ---------------- 投喂 ----------------
  function uploadFiles(files) {
    var list = $("uploadList");
    Array.prototype.forEach.call(files, function (file) {
      var li = document.createElement("li");
      li.innerHTML = "<span>" + esc(file.name) + "</span><span>读取中…</span>";
      list.appendChild(li);
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result).split(",")[1] || "";
        li.lastElementChild.textContent = "索引中…";
        postJSON("/api/kb/upload", { base: cur, name: file.name, data: b64 })
          .then(function (d) {
            li.lastElementChild.className = "ok";
            li.lastElementChild.textContent = "✓ " + d.doc.chunks + " 块";
            loadBases(true);
          })
          .catch(function (e) {
            li.lastElementChild.className = "err";
            li.lastElementChild.textContent = "✕ " + e.message;
          });
      };
      reader.onerror = function () { li.lastElementChild.textContent = "✕ 读取失败"; };
      reader.readAsDataURL(file);
    });
  }
  $("pickBtn").addEventListener("click", function (e) { e.stopPropagation(); $("fileInput").click(); });
  $("dropZone").addEventListener("click", function () { $("fileInput").click(); });
  $("fileInput").addEventListener("change", function () {
    uploadFiles(this.files); this.value = "";
  });
  ["dragenter", "dragover"].forEach(function (ev) {
    $("dropZone").addEventListener(ev, function (e) { e.preventDefault(); this.classList.add("is-over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    $("dropZone").addEventListener(ev, function (e) { e.preventDefault(); this.classList.remove("is-over"); });
  });
  $("dropZone").addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });

  // ---------------- 文档列表 ----------------
  function renderDocs(docs) {
    var box = $("docList");
    box.innerHTML = docs.length ? "" : '<p class="kb-hint">还没有文档，拖拽或选择文件投喂。</p>';
    var filter = $("chunkDocFilter");
    filter.innerHTML = '<option value="">全部文档</option>';
    docs.forEach(function (d) {
      var row = document.createElement("div");
      row.className = "kb-doc";
      row.innerHTML = '<span class="kb-doc-name">' + esc(d.name) + "</span>" +
        '<span class="kb-doc-meta">' + d.chunks + " 块 · " + d.chars + " 字</span>" +
        '<button class="kb-mini danger">删除</button>';
      row.querySelector("button").addEventListener("click", function () {
        if (confirm("删除文档「" + d.name + "」及其知识块？"))
          postJSON("/api/kb/doc/delete", { id: d.id, base: cur }).then(function () { loadBases(true); });
      });
      box.appendChild(row);
      var opt = document.createElement("option");
      opt.value = d.id; opt.textContent = d.name;
      filter.appendChild(opt);
    });
  }

  // ---------------- 手动知识点 ----------------
  $("manualAdd").addEventListener("click", function () {
    var text = $("manualText").value.trim();
    if (!text) { toast("知识点内容不能为空"); return; }
    postJSON("/api/kb/chunk/add", { base: cur, heading: $("manualHeading").value.trim(), text: text })
      .then(function () {
        $("manualText").value = ""; $("manualHeading").value = "";
        toast("已加入知识库"); loadBases(true);
      }).catch(function (e) { toast(e.message); });
  });

  // ---------------- 检索试验 ----------------
  function doSearch() {
    var q = $("searchInput").value.trim();
    if (!q) return;
    $("searchResults").innerHTML = '<p class="kb-hint">检索中…</p>';
    getJSON("/api/kb/search?base=" + cur + "&q=" + encodeURIComponent(q))
      .then(function (d) {
        var items = d.items || [];
        var box = $("searchResults");
        box.innerHTML = items.length ? "" : '<p class="kb-hint">没有命中，试试别的关键词或先投喂文档。</p>';
        items.forEach(function (h) {
          var el = document.createElement("div");
          el.className = "kb-result";
          el.innerHTML = '<div class="kb-result-head"><span>' + esc(h.doc_name || "") +
            (h.heading ? " · " + esc(h.heading) : "") + '</span><span class="score">' +
            h.score + "</span></div>" +
            '<div class="kb-result-body">' + esc(h.text) + "</div>";
          box.appendChild(el);
        });
      }).catch(function (e) { toast(e.message); });
  }
  $("searchBtn").addEventListener("click", doSearch);
  $("searchInput").addEventListener("keydown", function (e) { if (e.key === "Enter") doSearch(); });

  // ---------------- 知识块管理 ----------------
  function loadChunks() {
    var doc = $("chunkDocFilter").value;
    return getJSON("/api/kb/chunks?base=" + cur + (doc ? "&doc=" + doc : ""))
      .then(function (d) { renderChunks(d.items || []); });
  }
  $("chunkDocFilter").addEventListener("change", loadChunks);
  function renderChunks(chunks) {
    var box = $("chunkList");
    box.innerHTML = chunks.length ? "" : '<p class="kb-hint">暂无知识块。</p>';
    chunks.forEach(function (c) {
      var el = document.createElement("div");
      el.className = "kb-chunk";
      el.innerHTML =
        '<div class="kb-chunk-head"><span class="grow">' + esc(c.doc_name || "") +
        " · #" + c.ord + '</span><button class="kb-mini" data-a="save">保存</button>' +
        '<button class="kb-mini danger" data-a="del">删除</button></div>' +
        '<input class="kb-chunk-heading" value="' + esc(c.heading) + '" placeholder="标题/章节">' +
        '<textarea class="kb-chunk-text">' + esc(c.text) + "</textarea>";
      el.querySelector('[data-a="save"]').addEventListener("click", function () {
        postJSON("/api/kb/chunk/update", {
          id: c.id, base: cur,
          heading: el.querySelector(".kb-chunk-heading").value,
          text: el.querySelector(".kb-chunk-text").value
        }).then(function () { toast("已保存并重建索引"); loadChunks(); });
      });
      el.querySelector('[data-a="del"]').addEventListener("click", function () {
        if (confirm("删除这个知识块？"))
          postJSON("/api/kb/chunk/delete", { id: c.id, base: cur }).then(function () { loadBases(true); });
      });
      box.appendChild(el);
    });
  }

  loadBases(false);
})();