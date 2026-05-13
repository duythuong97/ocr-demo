/* knowledge.js — Knowledge Base CRUD page
 * Handles: Quick Add, Bulk Import, Relationships, Nodes, Text Knowledge
 */

(function () {
  "use strict";

  const app = document.getElementById("knowledgeApp");
  if (!app) return;

  const URL_NODES           = app.dataset.urlNodes;
  const URL_NODE_CREATE     = app.dataset.urlNodeCreate;
  const URL_EDGES           = app.dataset.urlEdges;
  const URL_EDGE_CREATE     = app.dataset.urlEdgeCreate;
  const URL_TEXTS           = app.dataset.urlTexts;
  const URL_TEXT_CREATE     = app.dataset.urlTextCreate;
  const URL_APPLY           = app.dataset.urlApply;
  const URL_TEMPLATES       = app.dataset.urlTemplates;
  const URL_QUICK_ADD       = app.dataset.urlQuickAdd;
  const URL_IMPORT_CSV      = app.dataset.urlImportCsv;
  const URL_IMPORT_TEMPLATE = app.dataset.urlImportTemplate;

  // ── Utilities ──────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showToast(msg, isErr = false) {
    const t = document.getElementById("kwToast");
    t.textContent = msg;
    t.className = "kw-toast" + (isErr ? " kw-toast-err" : " kw-toast-ok");
    t.style.display = "block";
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.style.display = "none"; }, 3200);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: opts.body && !(opts.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {},
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function fmtDate(s) {
    if (!s) return "—";
    return s.replace("T", " ").slice(0, 16);
  }

  function parseJsonInput(str, errorEl) {
    try {
      const v = JSON.parse(str || "{}");
      if (typeof v !== "object" || Array.isArray(v)) throw new Error("must be JSON object");
      errorEl.textContent = "";
      return v;
    } catch (e) {
      errorEl.textContent = "Invalid JSON: " + e.message;
      return null;
    }
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────

  const TAB_IDS = ["quick-add", "import", "edges", "nodes", "texts"];
  const tabs = document.querySelectorAll(".kw-tab");

  function activateTab(id) {
    tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === id));
    TAB_IDS.forEach(tid => {
      const p = document.getElementById("panel-" + tid);
      if (p) p.style.display = tid === id ? "block" : "none";
    });
  }

  tabs.forEach(t => {
    t.addEventListener("click", () => activateTab(t.dataset.tab));
  });

  // ── Apply All ──────────────────────────────────────────────────────────────

  document.getElementById("btnApplyAll").addEventListener("click", async () => {
    try {
      const r = await api(URL_APPLY, { method: "POST" });
      const g = r.result?.graph ?? {};
      const t = r.result?.texts ?? {};
      showToast(`Applied: ${g.nodes_written ?? 0} nodes, ${g.edges_written ?? 0} edges, ${t.texts_indexed ?? 0} texts indexed`);
    } catch (e) {
      showToast("Apply failed: " + e.message, true);
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // QUICK ADD — Template grid + simple form
  // ═══════════════════════════════════════════════════════════════════════════

  let _selectedTpl = null;
  let _allTemplates = [];

  async function loadTemplates() {
    try {
      const data = await api(URL_TEMPLATES);
      _allTemplates = data.templates || [];
      renderTemplateGrid(_allTemplates);
    } catch (e) {
      document.getElementById("templateGrid").innerHTML =
        `<div class="kw-empty">Failed to load templates: ${esc(e.message)}</div>`;
    }
  }

  function renderTemplateGrid(templates) {
    const grid = document.getElementById("templateGrid");
    if (!templates.length) {
      grid.innerHTML = '<div class="kw-empty">No templates available.</div>';
      return;
    }

    // Group by category
    const groups = {};
    for (const t of templates) {
      (groups[t.category] = groups[t.category] || []).push(t);
    }

    let html = "";
    for (const [cat, items] of Object.entries(groups)) {
      html += `<div class="kw-template-category">${esc(cat)}</div>
               <div class="kw-template-row">`;
      for (const t of items) {
        html += `<button class="kw-template-card" data-tpl-id="${esc(t.id)}" title="${esc(t.label)}">
          <span class="kw-template-icon">${t.icon}</span>
          <span class="kw-template-label">${esc(t.label)}</span>
          <span class="kw-rel-badge">${esc(t.rel_type)}</span>
        </button>`;
      }
      html += `</div>`;
    }
    grid.innerHTML = html;

    grid.querySelectorAll(".kw-template-card").forEach(card => {
      card.addEventListener("click", () => {
        const id = card.dataset.tplId;
        selectTemplate(id);
        grid.querySelectorAll(".kw-template-card").forEach(c => c.classList.remove("selected"));
        card.classList.add("selected");
      });
    });
  }

  function selectTemplate(id) {
    const tpl = _allTemplates.find(t => t.id === id);
    if (!tpl) return;
    _selectedTpl = tpl;

    document.getElementById("qaTplIcon").textContent  = tpl.icon;
    document.getElementById("qaTplLabel").textContent = tpl.label;
    document.getElementById("qaTplRel").textContent   = tpl.rel_type;
    document.getElementById("qaFromLabel").textContent = tpl.from_label;
    document.getElementById("qaToLabel").textContent   = tpl.to_label;
    document.getElementById("qaFromName").placeholder  = tpl.from_hint || tpl.from_label;
    document.getElementById("qaToName").placeholder    = tpl.to_hint   || tpl.to_label;
    document.getElementById("qaFromHint").textContent  = tpl.from_hint || "";
    document.getElementById("qaToHint").textContent    = tpl.to_hint   || "";
    document.getElementById("qaError").textContent     = "";
    document.getElementById("qaFromName").value = "";
    document.getElementById("qaToName").value   = "";

    const form = document.getElementById("quickAddForm");
    form.style.display = "block";
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
    document.getElementById("qaFromName").focus();
  }

  document.getElementById("btnQuickClear").addEventListener("click", () => {
    _selectedTpl = null;
    document.getElementById("quickAddForm").style.display = "none";
    document.querySelectorAll(".kw-template-card").forEach(c => c.classList.remove("selected"));
  });

  document.getElementById("btnQuickAdd").addEventListener("click", async () => {
    if (!_selectedTpl) return;
    const from_name  = document.getElementById("qaFromName").value.trim();
    const to_name    = document.getElementById("qaToName").value.trim();
    const repository = document.getElementById("qaRepository").value.trim() || "manual";
    const errEl      = document.getElementById("qaError");

    if (!from_name || !to_name) {
      errEl.textContent = "Both names are required";
      return;
    }
    errEl.textContent = "";

    try {
      await api(URL_QUICK_ADD, {
        method: "POST",
        body: JSON.stringify({ template_id: _selectedTpl.id, from_name, to_name, repository }),
      });
      showToast(`Added: ${from_name} → [${_selectedTpl.rel_type}] → ${to_name}`);
      document.getElementById("qaFromName").value = "";
      document.getElementById("qaToName").value   = "";
      // Refresh edges table in background
      loadEdges();
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  // Allow Enter key to submit quick-add form
  ["qaFromName", "qaToName", "qaRepository"].forEach(id => {
    document.getElementById(id).addEventListener("keydown", e => {
      if (e.key === "Enter") document.getElementById("btnQuickAdd").click();
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // BULK IMPORT — CSV file upload + preview
  // ═══════════════════════════════════════════════════════════════════════════

  let _importRows  = [];
  let _importHeaders = [];

  // Download template
  document.getElementById("btnDownloadTemplate").addEventListener("click", (e) => {
    e.preventDefault();
    window.location.href = URL_IMPORT_TEMPLATE;
  });

  // File input trigger via dropzone label
  const dropzone   = document.getElementById("importDropzone");
  const fileInput  = document.getElementById("importFileInput");

  dropzone.addEventListener("dragover", e => {
    e.preventDefault();
    dropzone.classList.add("kw-dropzone-hover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("kw-dropzone-hover"));
  dropzone.addEventListener("drop", e => {
    e.preventDefault();
    dropzone.classList.remove("kw-dropzone-hover");
    const f = e.dataTransfer.files[0];
    if (f) handleCsvFile(f);
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleCsvFile(fileInput.files[0]);
  });

  function handleCsvFile(file) {
    if (!file.name.toLowerCase().endsWith(".csv")) {
      showToast("Only .csv files accepted", true);
      return;
    }
    document.getElementById("dropzoneLabel").textContent = file.name;
    document.getElementById("importResults").style.display = "none";

    const reader = new FileReader();
    reader.onload = ev => {
      const text = ev.target.result;
      parseCsvPreview(text, file);
    };
    reader.readAsText(file);
  }

  function parseCsvText(text) {
    // Simple RFC-4180 parser
    const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    return lines.map(line => {
      const row = [];
      let inQuote = false, cur = "";
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') {
          if (inQuote && line[i + 1] === '"') { cur += '"'; i++; }
          else inQuote = !inQuote;
        } else if (ch === "," && !inQuote) {
          row.push(cur); cur = "";
        } else {
          cur += ch;
        }
      }
      row.push(cur);
      return row;
    }).filter(r => r.some(c => c.trim()));
  }

  function parseCsvPreview(text, file) {
    const rows = parseCsvText(text);
    if (rows.length < 2) {
      showToast("CSV has no data rows", true);
      return;
    }
    _importHeaders = rows[0].map(h => h.trim().toLowerCase());
    _importRows    = rows.slice(1);

    // Render header
    const thead = document.getElementById("importPreviewHead");
    thead.innerHTML = `<tr>${_importHeaders.map(h => `<th>${esc(h)}</th>`).join("")}</tr>`;

    // Render up to 5 preview rows
    const tbody = document.getElementById("importPreviewBody");
    const preview = _importRows.slice(0, 5);
    tbody.innerHTML = preview.map(r =>
      `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`
    ).join("");

    document.getElementById("importPreviewCount").textContent =
      `${_importRows.length} row${_importRows.length !== 1 ? "s" : ""}` +
      (_importRows.length > 5 ? ` (showing first 5)` : "");

    document.getElementById("importPreviewWrap").style.display = "block";

    // Store file for upload
    dropzone._file = file;
  }

  document.getElementById("btnClearImport").addEventListener("click", () => {
    _importRows = [];
    _importHeaders = [];
    fileInput.value = "";
    dropzone._file = null;
    document.getElementById("dropzoneLabel").textContent = "Drop CSV here or click to browse";
    document.getElementById("importPreviewWrap").style.display = "none";
    document.getElementById("importResults").style.display = "none";
  });

  document.getElementById("btnRunImport").addEventListener("click", async () => {
    const file = dropzone._file;
    if (!file) { showToast("No file selected", true); return; }

    const btn = document.getElementById("btnRunImport");
    btn.disabled = true;
    btn.textContent = "Importing…";

    try {
      const fd = new FormData();
      fd.append("file", file);

      const res = await fetch(URL_IMPORT_CSV, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

      const resultsEl = document.getElementById("importResults");
      const errList = (data.errors || []).slice(0, 10);
      resultsEl.innerHTML =
        `<div class="kw-import-summary ${data.skipped ? 'kw-import-partial' : 'kw-import-success'}">
           <strong>✓ ${data.created} relationship${data.created !== 1 ? "s" : ""} imported</strong>
           ${data.skipped ? `<span class="kw-import-skip">${data.skipped} row${data.skipped !== 1 ? "s" : ""} skipped</span>` : ""}
         </div>` +
        (errList.length
          ? `<ul class="kw-import-errors">${errList.map(e => `<li>${esc(e)}</li>`).join("")}</ul>` +
            (data.errors.length > 10 ? `<p class="kw-hint">…and ${data.errors.length - 10} more</p>` : "")
          : "");
      resultsEl.style.display = "block";
      resultsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });

      showToast(`Imported ${data.created} relationships`);
      loadEdges();
    } catch (e) {
      showToast("Import failed: " + e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Import";
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // NODES
  // ═══════════════════════════════════════════════════════════════════════════

  let _nodeFilterTimer = null;

  async function loadNodes() {
    const q = document.getElementById("nodeSearch").value.trim();
    const label = document.getElementById("nodeLabelFilter").value.trim();
    const url = URL_NODES + `?q=${encodeURIComponent(q)}&label=${encodeURIComponent(label)}`;
    try {
      const data = await api(url);
      renderNodes(data.nodes || []);
    } catch (e) {
      showToast("Failed to load nodes: " + e.message, true);
    }
  }

  function renderNodes(rows) {
    const tbody = document.getElementById("nodeTbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="kw-empty">No nodes found.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(n => {
      const props = n.properties_json && n.properties_json !== "{}" ? `<code class="kw-props">${esc(n.properties_json)}</code>` : "—";
      return `<tr>
        <td><span class="kw-badge">${esc(n.label)}</span></td>
        <td class="kw-mono">${esc(n.name)}</td>
        <td>${esc(n.repository)}</td>
        <td>${props}</td>
        <td class="kw-date">${fmtDate(n.updated_at)}</td>
        <td class="kw-actions">
          <button class="btn-icon" title="Edit" onclick="KW.editNode(${n.id})">✏️</button>
          <button class="btn-icon btn-danger" title="Delete" onclick="KW.deleteNode(${n.id}, '${esc(n.name)}')">🗑</button>
        </td>
      </tr>`;
    }).join("");
  }

  document.getElementById("nodeSearch").addEventListener("input", () => {
    clearTimeout(_nodeFilterTimer);
    _nodeFilterTimer = setTimeout(loadNodes, 300);
  });
  document.getElementById("nodeLabelFilter").addEventListener("input", () => {
    clearTimeout(_nodeFilterTimer);
    _nodeFilterTimer = setTimeout(loadNodes, 300);
  });

  // Form show/hide
  const nodeForm = document.getElementById("nodeForm");
  document.getElementById("btnNewNode").addEventListener("click", () => {
    resetNodeForm();
    nodeForm.style.display = "block";
    nodeForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  document.getElementById("btnNodeCancel").addEventListener("click", () => {
    nodeForm.style.display = "none";
  });

  function resetNodeForm() {
    document.getElementById("nodeFormId").value = "";
    document.getElementById("nodeFormTitle").textContent = "New Graph Node";
    document.getElementById("nodeLabel").value = "";
    document.getElementById("nodeName").value = "";
    document.getElementById("nodeRepository").value = "manual";
    document.getElementById("nodeProps").value = "{}";
    document.getElementById("nodeError").textContent = "";
  }

  window.KW = window.KW || {};

  window.KW.editNode = async function(id) {
    try {
      const all = await api(URL_NODES + "?limit=10000");
      const n = (all.nodes || []).find(x => x.id === id);
      if (!n) throw new Error("not found");
      document.getElementById("nodeFormId").value = n.id;
      document.getElementById("nodeFormTitle").textContent = "Edit Graph Node";
      document.getElementById("nodeLabel").value = n.label;
      document.getElementById("nodeName").value = n.name;
      document.getElementById("nodeRepository").value = n.repository;
      document.getElementById("nodeProps").value = n.properties_json || "{}";
      document.getElementById("nodeError").textContent = "";
      nodeForm.style.display = "block";
      nodeForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      showToast("Failed to load node: " + e.message, true);
    }
  };

  window.KW.deleteNode = async function(id, name) {
    if (!confirm(`Delete node "${name}"?`)) return;
    try {
      await api(`${URL_NODE_CREATE}/${id}`, { method: "DELETE" });
      showToast("Node deleted");
      loadNodes();
    } catch (e) {
      showToast("Delete failed: " + e.message, true);
    }
  };

  document.getElementById("btnNodeSave").addEventListener("click", async () => {
    const id = document.getElementById("nodeFormId").value;
    const label = document.getElementById("nodeLabel").value.trim();
    const name = document.getElementById("nodeName").value.trim();
    const repository = document.getElementById("nodeRepository").value.trim() || "manual";
    const errEl = document.getElementById("nodeError");
    const properties = parseJsonInput(document.getElementById("nodeProps").value, errEl);
    if (!label || !name) { errEl.textContent = "Label and name are required"; return; }
    if (properties === null) return;
    try {
      if (id) {
        await api(`${URL_NODE_CREATE}/${id}`, { method: "PUT", body: JSON.stringify({ name, repository, properties }) });
        showToast("Node updated");
      } else {
        await api(URL_NODE_CREATE, { method: "POST", body: JSON.stringify({ label, name, repository, properties }) });
        showToast("Node created");
      }
      nodeForm.style.display = "none";
      loadNodes();
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // EDGES
  // ═══════════════════════════════════════════════════════════════════════════

  let _edgeFilterTimer = null;

  async function loadEdges() {
    const q = document.getElementById("edgeSearch").value.trim();
    const rel_type = document.getElementById("edgeRelFilter").value.trim();
    const url = URL_EDGES + `?q=${encodeURIComponent(q)}&rel_type=${encodeURIComponent(rel_type)}`;
    try {
      const data = await api(url);
      renderEdges(data.edges || []);
    } catch (e) {
      showToast("Failed to load edges: " + e.message, true);
    }
  }

  function _shortQname(qname) {
    // "Function:repo:MyClass.method" → "MyClass.method"
    const parts = qname.split(":");
    return parts.length >= 3 ? parts.slice(2).join(":") : qname;
  }

  function renderEdges(rows) {
    const tbody = document.getElementById("edgeTbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="kw-empty">No relationships found.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(e => {
      const fromShort = _shortQname(e.from_qname);
      const toShort   = _shortQname(e.to_qname);
      return `<tr>
        <td class="kw-mono kw-truncate" title="${esc(e.from_qname)}">${esc(fromShort)}</td>
        <td><span class="kw-badge kw-badge-rel">${esc(e.rel_type)}</span></td>
        <td class="kw-mono kw-truncate" title="${esc(e.to_qname)}">${esc(toShort)}</td>
        <td class="kw-date">${fmtDate(e.created_at)}</td>
        <td class="kw-actions">
          <button class="btn-icon btn-danger" title="Delete" onclick="KW.deleteEdge(${e.id}, '${esc(fromShort)}→${esc(toShort)}')">🗑</button>
        </td>
      </tr>`;
    }).join("");
  }

  document.getElementById("edgeSearch").addEventListener("input", () => {
    clearTimeout(_edgeFilterTimer);
    _edgeFilterTimer = setTimeout(loadEdges, 300);
  });
  document.getElementById("edgeRelFilter").addEventListener("input", () => {
    clearTimeout(_edgeFilterTimer);
    _edgeFilterTimer = setTimeout(loadEdges, 300);
  });

  const edgeForm = document.getElementById("edgeForm");
  document.getElementById("btnNewEdge").addEventListener("click", () => {
    document.getElementById("edgeFrom").value = "";
    document.getElementById("edgeRelType").value = "";
    document.getElementById("edgeTo").value = "";
    document.getElementById("edgeProps").value = "{}";
    document.getElementById("edgeError").textContent = "";
    edgeForm.style.display = "block";
    edgeForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  document.getElementById("btnEdgeCancel").addEventListener("click", () => {
    edgeForm.style.display = "none";
  });

  window.KW.deleteEdge = async function(id, label) {
    if (!confirm(`Delete edge "${label}"?`)) return;
    try {
      await api(`${URL_EDGE_CREATE}/${id}`, { method: "DELETE" });
      showToast("Edge deleted");
      loadEdges();
    } catch (e) {
      showToast("Delete failed: " + e.message, true);
    }
  };

  document.getElementById("btnEdgeSave").addEventListener("click", async () => {
    const from_qname = document.getElementById("edgeFrom").value.trim();
    const rel_type = document.getElementById("edgeRelType").value.trim().toUpperCase();
    const to_qname = document.getElementById("edgeTo").value.trim();
    const errEl = document.getElementById("edgeError");
    const properties = parseJsonInput(document.getElementById("edgeProps").value, errEl);
    if (!from_qname || !rel_type || !to_qname) { errEl.textContent = "All fields are required"; return; }
    if (properties === null) return;
    try {
      await api(URL_EDGE_CREATE, { method: "POST", body: JSON.stringify({ from_qname, to_qname, rel_type, properties }) });
      showToast("Edge created");
      edgeForm.style.display = "none";
      loadEdges();
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // TEXTS
  // ═══════════════════════════════════════════════════════════════════════════

  let _textFilterTimer = null;

  async function loadTexts() {
    const q = document.getElementById("textSearch").value.trim();
    const tags = document.getElementById("textTagFilter").value.trim();
    const url = URL_TEXTS + `?q=${encodeURIComponent(q)}&tags=${encodeURIComponent(tags)}`;
    try {
      const data = await api(url);
      renderTexts(data.texts || []);
    } catch (e) {
      showToast("Failed to load texts: " + e.message, true);
    }
  }

  function renderTexts(rows) {
    const tbody = document.getElementById("textTbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="kw-empty">No text knowledge found.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(t => {
      const indexed = t.indexed_at ? `<span class="kw-indexed">✓ ${fmtDate(t.indexed_at)}</span>` : `<span class="kw-not-indexed">pending</span>`;
      const tags = t.tags ? t.tags.split(",").map(x => `<span class="kw-tag">${esc(x.trim())}</span>`).join(" ") : "—";
      return `<tr>
        <td>${esc(t.title)}</td>
        <td>${tags}</td>
        <td>${esc(t.repository)}</td>
        <td>${indexed}</td>
        <td class="kw-date">${fmtDate(t.updated_at)}</td>
        <td class="kw-actions">
          <button class="btn-icon" title="Edit" onclick="KW.editText(${t.id})">✏️</button>
          <button class="btn-icon btn-danger" title="Delete" onclick="KW.deleteText(${t.id}, '${esc(t.title)}')">🗑</button>
        </td>
      </tr>`;
    }).join("");
  }

  document.getElementById("textSearch").addEventListener("input", () => {
    clearTimeout(_textFilterTimer);
    _textFilterTimer = setTimeout(loadTexts, 300);
  });
  document.getElementById("textTagFilter").addEventListener("input", () => {
    clearTimeout(_textFilterTimer);
    _textFilterTimer = setTimeout(loadTexts, 300);
  });

  const textForm = document.getElementById("textForm");
  document.getElementById("btnNewText").addEventListener("click", () => {
    resetTextForm();
    textForm.style.display = "block";
    textForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  document.getElementById("btnTextCancel").addEventListener("click", () => {
    textForm.style.display = "none";
  });

  function resetTextForm() {
    document.getElementById("textFormId").value = "";
    document.getElementById("textFormTitle").textContent = "New Text Knowledge";
    document.getElementById("textTitle").value = "";
    document.getElementById("textContent").value = "";
    document.getElementById("textTags").value = "";
    document.getElementById("textRepository").value = "knowledge";
    document.getElementById("textError").textContent = "";
  }

  window.KW.editText = async function(id) {
    try {
      const t = await api(`${URL_TEXT_CREATE}/${id}`);
      document.getElementById("textFormId").value = t.id;
      document.getElementById("textFormTitle").textContent = "Edit Text Knowledge";
      document.getElementById("textTitle").value = t.title;
      document.getElementById("textContent").value = t.content;
      document.getElementById("textTags").value = t.tags || "";
      document.getElementById("textRepository").value = t.repository || "knowledge";
      document.getElementById("textError").textContent = "";
      textForm.style.display = "block";
      textForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      showToast("Failed to load text: " + e.message, true);
    }
  };

  window.KW.deleteText = async function(id, title) {
    if (!confirm(`Delete "${title}"? It will be removed from search indexes.`)) return;
    try {
      await api(`${URL_TEXT_CREATE}/${id}`, { method: "DELETE" });
      showToast("Text deleted");
      loadTexts();
    } catch (e) {
      showToast("Delete failed: " + e.message, true);
    }
  };

  document.getElementById("btnTextSave").addEventListener("click", async () => {
    const id = document.getElementById("textFormId").value;
    const title = document.getElementById("textTitle").value.trim();
    const content = document.getElementById("textContent").value.trim();
    const tags = document.getElementById("textTags").value.trim();
    const repository = document.getElementById("textRepository").value.trim() || "knowledge";
    const errEl = document.getElementById("textError");
    if (!title || !content) { errEl.textContent = "Title and content are required"; return; }
    try {
      if (id) {
        await api(`${URL_TEXT_CREATE}/${id}`, { method: "PUT", body: JSON.stringify({ title, content, tags, repository }) });
        showToast("Text updated and re-indexed");
      } else {
        await api(URL_TEXT_CREATE, { method: "POST", body: JSON.stringify({ title, content, tags, repository }) });
        showToast("Text created and indexed");
      }
      textForm.style.display = "none";
      loadTexts();
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  // ── Initial load ───────────────────────────────────────────────────────────
  loadTemplates();
  loadEdges();
  loadNodes();
  loadTexts();

})();
