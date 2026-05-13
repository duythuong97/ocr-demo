const root = document.querySelector(".indexing-main");
const statusUrl = root?.dataset.statusUrl;
const startUrl = root?.dataset.startUrl;
const stopUrl = root?.dataset.stopUrl;
const sourcesUrl = root?.dataset.sourcesUrl;
const addSourceUrl = root?.dataset.addSourceUrl;
const runAllUrl = root?.dataset.runAllUrl;
const clearSolrUrl = root?.dataset.clearSolrUrl;
const clearQdrantUrl = root?.dataset.clearQdrantUrl;
const clearSqliteUrl = root?.dataset.clearSqliteUrl;
const clearNeo4jUrl = root?.dataset.clearNeo4jUrl;
const clearAllUrl = root?.dataset.clearAllUrl;
const debugParsePathUrl = root?.dataset.debugParsePathUrl;
const socketIoPath = root?.dataset.socketIoPath || "/socket.io";

const form = document.getElementById("indexingForm");
const stopBtn = document.getElementById("stopBtn");
const runAllBtn = document.getElementById("runAllBtn");
const workerState = document.getElementById("workerState");
const activeJob = document.getElementById("activeJob");
const jobsList = document.getElementById("jobsList");
const filesList = document.getElementById("filesList");
const sourceSelect = document.getElementById("sourceSelect");
const reloadSourcesBtn = document.getElementById("reloadSourcesBtn");
const saveSourceBtn = document.getElementById("saveSourceBtn");
const runAllCard = document.getElementById("runAllCard");
const runAllProgress = document.getElementById("runAllProgress");
const runAllLog = document.getElementById("runAllLog");

let allSources = [];

function esc(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

const STATUS_ICON = {
  completed: "✓",
  running: "⟳",
  scanning: "⟳",
  failed: "✗",
  cancelled: "⊘",
  queued: "…",
};

function formatJob(job) {
  const icon = STATUS_ICON[job.status] ?? "?";
  const statusBadge = `<span class="job-status job-status--${esc(job.status)}">${icon} ${esc(job.status)}</span>`;

  const total = job.total_files ?? 0;
  const processed = job.processed_files ?? 0;
  const queued = job.queued_files ?? 0;
  const newFiles = job.indexed_files ?? 0;
  const updated = job.updated_files ?? 0;
  const failed = job.failed_files ?? 0;
  const skipped = job.skipped_files ?? 0;

  const statsHtml = `
    <div class="job-stats">
      <span class="stat">📄 Total <strong>${total}</strong></span>
      <span class="stat">⏳ Queued <strong>${queued}</strong></span>
      <span class="stat">✅ New <strong>${newFiles}</strong></span>
      <span class="stat">🔄 Updated <strong>${updated}</strong></span>
      <span class="stat stat--fail">❌ Failed <strong>${failed}</strong></span>
      <span class="stat">⏭ Skipped <strong>${skipped}</strong></span>
    </div>`;

  const progress =
    queued > 0
      ? `<div class="job-progress-bar"><div class="job-progress-fill" style="width:${Math.round((processed / queued) * 100)}%"></div></div>`
      : "";

  const metaLines = [
    `<span>📁 ${esc(job.root_path)}</span>`,
    job.current_file ? `<span>🔍 ${esc(job.current_file)}</span>` : "",
    job.last_error
      ? `<span class="error-text">⚠ ${esc(job.last_error)}</span>`
      : "",
    job.started_at
      ? `<span>▶ Started: ${esc(fmtDate(job.started_at))}</span>`
      : "",
    job.finished_at
      ? `<span>⏹ Finished: ${esc(fmtDate(job.finished_at))}</span>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  return `<div class="indexing-item">
    <div class="job-header"><span class="job-id">#${job.id}</span>${statusBadge}</div>
    ${statsHtml}
    ${progress}
    <div class="job-meta">${metaLines}</div>
  </div>`;
}

function formatFile(row) {
  const statusIcon = row.last_status === "done" ? "✅" : "❌";
  const info = `${esc(row.repository || "-")} | ${statusIcon} ${esc(row.last_status)} | ${esc(fmtDate(row.last_indexed_at))}`;
  const error = row.last_error
    ? `<div class="error-text">⚠ ${esc(row.last_error)}</div>`
    : "";
  const parseBtn = debugParsePathUrl
    ? `<button type="button" class="btn btn-sm file-parse-btn" data-file-path="${esc(row.file_path)}">Parse</button>`
    : "";
  return `<div class="indexing-item">
    <div class="indexing-item-row">
      <strong>${esc(row.rel_path || row.file_path)}</strong>
      ${parseBtn}
    </div>
    <div class="muted">${info}</div>
    ${error}
  </div>`;
}

async function loadStatus() {
  try {
    const res = await fetch(statusUrl, { cache: "no-store" });
    const data = await res.json();
    renderStatus(data);
  } catch (err) {
    workerState.textContent = `Status error: ${err}`;
  }
}

function renderStatus(data) {
  workerState.textContent = data.worker_running ? "Running" : "Stopped";

  if (data.active_job) {
    activeJob.innerHTML = formatJob(data.active_job);
  } else {
    activeJob.textContent = "No active job";
  }

  jobsList.innerHTML = data.recent_jobs.length
    ? data.recent_jobs.map(formatJob).join("")
    : '<div class="muted">No jobs yet.</div>';

  filesList.innerHTML = data.recent_files.length
    ? data.recent_files.map(formatFile).join("")
    : '<div class="muted">No indexed files yet.</div>';

  // Run-all panel
  const ra = data.run_all;
  if (ra && (ra.running || ra.log?.length > 0)) {
    runAllCard.style.display = "";
    const pct = ra.total > 0 ? Math.round((ra.done / ra.total) * 100) : 0;
    runAllProgress.innerHTML = ra.running
      ? `<div class="run-all-status running">⟳ Running… ${ra.done}/${ra.total} sources <span class="run-all-pct">${pct}%</span></div>
           <div class="job-progress-bar"><div class="job-progress-fill" style="width:${pct}%"></div></div>`
      : `<div class="run-all-status done">✓ Done — ${ra.done}/${ra.total} sources processed</div>`;
    runAllLog.innerHTML = (ra.log || [])
      .map((line) => `<div class="run-all-line">${esc(line)}</div>`)
      .join("");
    // Auto-scroll log to bottom
    runAllLog.scrollTop = runAllLog.scrollHeight;

    // Disable/enable Run All button
    if (runAllBtn) runAllBtn.disabled = ra.running;
  } else {
    if (runAllBtn) runAllBtn.disabled = false;
  }
}

function getSourceLabel(source) {
  const name =
    source.name ||
    source.repository ||
    source.repository_path ||
    source.root_path;
  const type = source.is_default ? "default" : "custom";
  return `${name} (${type})`;
}

function fillFormFromSource(source) {
  if (!form || !source) return;
  form.elements.root_path.value = source.root_path || "";
  form.elements.name.value =
    source.name || source.repository || source.repository_path || "";
  form.elements.repository_url_base.value = source.repository_url_base || "";
}

async function loadSources() {
  if (!sourcesUrl || !sourceSelect) {
    return;
  }
  const res = await fetch(sourcesUrl, { cache: "no-store" });
  const data = await res.json();
  allSources = Array.isArray(data.sources) ? data.sources : [];

  sourceSelect.innerHTML = '<option value="">-- Select a source --</option>';
  allSources.forEach((source, idx) => {
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = getSourceLabel(source);
    sourceSelect.appendChild(opt);
  });
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(form).entries());

  const res = await fetch(startUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || "Failed to start indexing job.");
    return;
  }

  await loadStatus();
});

stopBtn?.addEventListener("click", async () => {
  const res = await fetch(stopUrl, { method: "POST" });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || "Failed to stop job.");
    return;
  }
  await loadStatus();
});

runAllBtn?.addEventListener("click", async () => {
  if (!runAllUrl) return;
  if (
    !confirm(
      `Run git pull / svn update on all sources, then queue all for indexing?`,
    )
  )
    return;
  runAllBtn.disabled = true;
  if (runAllCard) runAllCard.style.display = "";
  if (runAllProgress)
    runAllProgress.innerHTML =
      '<div class="run-all-status running">⟳ Starting…</div>';
  if (runAllLog) runAllLog.innerHTML = "";
  const res = await fetch(runAllUrl, { method: "POST" });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || "Failed to start run-all.");
    runAllBtn.disabled = false;
    return;
  }
  await loadStatus();
});

sourceSelect?.addEventListener("change", () => {
  const idx = Number(sourceSelect.value);
  if (!Number.isInteger(idx) || idx < 0 || idx >= allSources.length) {
    return;
  }
  fillFormFromSource(allSources[idx]);
});

reloadSourcesBtn?.addEventListener("click", async () => {
  try {
    await loadSources();
  } catch (err) {
    alert(`Failed to load sources: ${err}`);
  }
});

saveSourceBtn?.addEventListener("click", async () => {
  if (!form) {
    return;
  }
  const payload = Object.fromEntries(new FormData(form).entries());
  if (!payload.name?.trim()) {
    alert("Please enter Source Name before adding repository.");
    return;
  }

  const res = await fetch(addSourceUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || "Failed to add repository source.");
    return;
  }

  await loadSources();
  alert("Repository source added successfully.");
});

loadStatus();
loadSources();

// ── Clear Data ──
async function doClear(url, label) {
  const clearStatus = document.getElementById("clearStatus");
  if (!url) return;
  if (!confirm(`This will permanently delete all ${label} data. Continue?`))
    return;
  if (clearStatus) clearStatus.textContent = `Clearing ${label}…`;
  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      const msg = data.errors
        ? data.errors.join("; ")
        : data.error || "Unknown error";
      if (clearStatus) clearStatus.textContent = `Error: ${msg}`;
      alert(`Failed to clear ${label}: ${msg}`);
      return;
    }
    if (clearStatus)
      clearStatus.textContent = data.message || `${label} cleared.`;
    await loadStatus();
  } catch (err) {
    if (clearStatus) clearStatus.textContent = `Error: ${err}`;
    alert(`Failed to clear ${label}: ${err}`);
  }
}

document
  .getElementById("clearSolrBtn")
  ?.addEventListener("click", () => doClear(clearSolrUrl, "Solr"));
document
  .getElementById("clearQdrantBtn")
  ?.addEventListener("click", () => doClear(clearQdrantUrl, "Qdrant"));
document
  .getElementById("clearSqliteBtn")
  ?.addEventListener("click", () => doClear(clearSqliteUrl, "SQLite"));
document
  .getElementById("clearNeo4jBtn")
  ?.addEventListener("click", () => doClear(clearNeo4jUrl, "Neo4j"));
document
  .getElementById("clearAllBtn")
  ?.addEventListener("click", () =>
    doClear(clearAllUrl, "All (Solr + Qdrant + SQLite + Neo4j)"),
  );

// ── Socket.IO ──
const socket = io({ path: socketIoPath, transports: ["websocket", "polling"] });
socket.on("indexing_status", (data) => renderStatus(data));
socket.on("connect_error", () => {
  // Fall back to polling when socket is unavailable
  if (!window._statusPollFallback) {
    window._statusPollFallback = setInterval(loadStatus, 5000);
  }
});
socket.on("connect", () => {
  if (window._statusPollFallback) {
    clearInterval(window._statusPollFallback);
    window._statusPollFallback = null;
  }
});

// ── Debug File Parser ─────────────────────────────────────────────────────────
const debugParseUrl = root?.dataset.debugParseUrl;
const debugParseBtn = document.getElementById("debugParseBtn");
const debugFileInput = document.getElementById("debugFileInput");
const debugParseStatus = document.getElementById("debugParseStatus");
const debugResults = document.getElementById("debugResults");

const CHUNK_PREVIEW_LEN = 400;

function renderDebugResult(data) {
  // Reader meta
  const readerMeta = document.getElementById("debugReaderMeta");
  if (readerMeta) {
    const chunkerBadge = data.chunker_name
      ? `&nbsp;<span class="chunk-type-badge" style="opacity:0.75">${esc(data.chunker_name)}</span>`
      : "";
    readerMeta.innerHTML =
      `<span class="chunk-type-badge">${esc(data.reader_name)}</span>${chunkerBadge}&nbsp; ` +
      `<span class="muted">file: <strong>${esc(data.file_name)}</strong></span>`;
  }

  // read_content
  const contentCharsEl = document.getElementById("debugContentChars");
  const contentPre = document.getElementById("debugContentPre");
  if (contentCharsEl)
    contentCharsEl.textContent = `(${data.content_chars.toLocaleString()} chars)`;
  if (contentPre) contentPre.textContent = data.content_text;

  // read_semantic
  const semanticCharsEl = document.getElementById("debugSemanticChars");
  const semanticPre = document.getElementById("debugSemanticPre");
  if (semanticCharsEl)
    semanticCharsEl.textContent = `(${data.semantic_chars.toLocaleString()} chars)`;
  if (semanticPre) semanticPre.textContent = data.semantic_text;

  // Chunks header
  const chunksHeader = document.getElementById("debugChunksHeader");
  if (chunksHeader)
    chunksHeader.textContent = `${data.chunk_count} chunk${data.chunk_count !== 1 ? "s" : ""}`;

  // Chunks list
  const chunksList = document.getElementById("debugChunksList");
  if (!chunksList) return;

  if (!data.chunks.length) {
    chunksList.innerHTML = '<div class="muted">No chunks produced.</div>';
    return;
  }

  chunksList.innerHTML = data.chunks
    .map((c) => {
      const preview =
        c.text.length > CHUNK_PREVIEW_LEN
          ? esc(c.text.slice(0, CHUNK_PREVIEW_LEN)) +
            '<span class="muted">…</span>'
          : esc(c.text);
      const hasMore = c.text.length > CHUNK_PREVIEW_LEN;
      const expandBtn = hasMore
        ? `<button type="button" class="debug-expand-btn" data-chunk-idx="${c.index}">Show full</button>`
        : "";
      return `<div class="indexing-item debug-chunk-item" data-chunk-idx="${c.index}">
      <div class="debug-chunk-header">
        <span class="job-id">#${c.index + 1}</span>
        <span class="chunk-type-badge">${esc(c.chunk_type)}</span>
        <span class="muted" style="font-size:0.75rem;">${c.chars.toLocaleString()} chars</span>
        <span class="muted mono" style="font-size:0.72rem; margin-left:auto;">${esc(c.chunk_id)}</span>
      </div>
      <pre class="debug-chunk-text mono" data-full="${esc(c.text)}" data-expanded="false">${preview}</pre>
      ${expandBtn}
    </div>`;
    })
    .join("");

  // Expand button handlers
  chunksList.querySelectorAll(".debug-expand-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = btn.dataset.chunkIdx;
      const item = chunksList.querySelector(
        `.debug-chunk-item[data-chunk-idx="${idx}"]`,
      );
      const pre = item?.querySelector(".debug-chunk-text");
      if (!pre) return;
      if (pre.dataset.expanded === "true") {
        const fullText = pre.dataset.full;
        pre.innerHTML =
          esc(fullText.slice(0, CHUNK_PREVIEW_LEN)) +
          '<span class="muted">…</span>';
        pre.dataset.expanded = "false";
        btn.textContent = "Show full";
      } else {
        pre.textContent = pre.dataset.full;
        pre.dataset.expanded = "true";
        btn.textContent = "Collapse";
      }
    });
  });

  if (debugResults) debugResults.style.display = "";

  // ── Extraction (graph nodes + edges) ────────────────────────────────────────
  const extractSection = document.getElementById("debugExtractSection");
  const extractHeader = document.getElementById("debugExtractHeader");
  const nodesList = document.getElementById("debugNodesList");
  const edgesHeader = document.getElementById("debugEdgesHeader");
  const edgesList = document.getElementById("debugEdgesList");

  const ext = data.extraction;
  if (!ext || !ext.available || (!ext.extractor_names?.length && !ext.nodes?.length && !ext.edges?.length)) {
    if (extractSection) extractSection.style.display = "none";
  } else {
    if (extractSection) extractSection.style.display = "";
    const namesStr = ext.extractor_names?.length
      ? ext.extractor_names.map((n) => `<span class="chunk-type-badge" style="opacity:0.8">${esc(n)}</span>`).join(" ")
      : '<span class="muted">none matched</span>';
    if (extractHeader)
      extractHeader.innerHTML = `Extracted — ${(ext.nodes?.length || 0)} node${(ext.nodes?.length || 0) !== 1 ? "s" : ""}, ${(ext.edges?.length || 0)} edge${(ext.edges?.length || 0) !== 1 ? "s" : ""} &nbsp;·&nbsp; ${namesStr}`;

    if (nodesList) {
      if (!ext.nodes?.length) {
        nodesList.innerHTML = '<div class="muted">No nodes extracted.</div>';
      } else {
        nodesList.innerHTML = ext.nodes.map((n, i) => {
          const props = Object.entries(n.properties || {})
            .filter(([, v]) => v !== null && v !== undefined && v !== "")
            .map(([k, v]) => `<span class="muted">${esc(k)}:</span> ${esc(String(v))}`)
            .join(" &nbsp;·&nbsp; ");
          return `<div class="indexing-item debug-chunk-item">
            <div class="debug-chunk-header">
              <span class="job-id">#${i + 1}</span>
              <span class="chunk-type-badge">${esc(n.label)}</span>
              <span class="mono" style="font-size:0.8rem;">${esc(n.key_value)}</span>
              <span class="muted mono" style="font-size:0.72rem; margin-left:auto;">${esc(n.extractor)}</span>
            </div>
            ${props ? `<div class="debug-chunk-text mono" style="font-size:0.78rem; white-space:normal; word-break:break-word;">${props}</div>` : ""}
          </div>`;
        }).join("");
      }
    }

    if (edgesHeader)
      edgesHeader.textContent = `${ext.edges?.length || 0} relationship${(ext.edges?.length || 0) !== 1 ? "s" : ""}`;
    if (edgesList) {
      if (!ext.edges?.length) {
        edgesList.innerHTML = '<div class="muted">No relationships extracted.</div>';
      } else {
        edgesList.innerHTML = ext.edges.map((e, i) => {
          return `<div class="indexing-item debug-chunk-item">
            <div class="debug-chunk-header">
              <span class="job-id">#${i + 1}</span>
              <span class="chunk-type-badge" style="background:var(--accent-secondary, #5a4fcf);">${esc(e.rel_type)}</span>
              <span class="mono" style="font-size:0.8rem;">${esc(e.from_label)}(<em>${esc(e.from_key_value)}</em>)</span>
              <span class="muted">→</span>
              <span class="mono" style="font-size:0.8rem;">${esc(e.to_label)}(<em>${esc(e.to_key_value)}</em>)</span>
              <span class="muted mono" style="font-size:0.72rem; margin-left:auto;">${esc(e.extractor)}</span>
            </div>
          </div>`;
        }).join("");
      }
    }
  }
}

debugParseBtn?.addEventListener("click", async () => {
  const file = debugFileInput?.files?.[0];
  if (!file) {
    if (debugParseStatus)
      debugParseStatus.textContent = "Please select a file first.";
    return;
  }
  if (!debugParseUrl) return;

  if (debugParseStatus) debugParseStatus.textContent = "Parsing…";
  if (debugResults) debugResults.style.display = "none";
  debugParseBtn.disabled = true;

  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(debugParseUrl, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      if (debugParseStatus)
        debugParseStatus.textContent = `Error: ${data.error || "Unknown error"}`;
      return;
    }
    if (debugParseStatus)
      debugParseStatus.textContent = `Done — ${data.chunk_count} chunk${data.chunk_count !== 1 ? "s" : ""}, ${data.semantic_chars.toLocaleString()} chars`;
    renderDebugResult(data);
  } catch (err) {
    if (debugParseStatus) debugParseStatus.textContent = `Error: ${err}`;
  } finally {
    debugParseBtn.disabled = false;
  }
});

// ── Parse button in Recently Indexed Files ────────────────────────────────────
filesList?.addEventListener("click", async (e) => {
  const btn = e.target.closest(".file-parse-btn");
  if (!btn || !debugParsePathUrl) return;

  const filePath = btn.dataset.filePath;
  if (!filePath) return;

  btn.disabled = true;
  btn.textContent = "Parsing…";
  if (debugResults) debugResults.style.display = "none";
  if (debugParseStatus) debugParseStatus.textContent = "";

  try {
    const res = await fetch(debugParsePathUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      if (debugParseStatus)
        debugParseStatus.textContent = `Error: ${data.error || "Unknown error"}`;
      btn.textContent = "Parse";
      btn.disabled = false;
      return;
    }
    if (debugParseStatus)
      debugParseStatus.textContent = `Done — ${data.chunk_count} chunk${data.chunk_count !== 1 ? "s" : ""}, ${data.semantic_chars.toLocaleString()} chars`;
    renderDebugResult(data);
    // Scroll to Debug section
    debugResults?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    if (debugParseStatus) debugParseStatus.textContent = `Error: ${err}`;
  } finally {
    btn.textContent = "Parse";
    btn.disabled = false;
  }
});
