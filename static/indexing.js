const root = document.querySelector('.indexing-main');
const statusUrl = root?.dataset.statusUrl;
const startUrl = root?.dataset.startUrl;
const stopUrl = root?.dataset.stopUrl;
const sourcesUrl = root?.dataset.sourcesUrl;
const addSourceUrl = root?.dataset.addSourceUrl;
const clearSolrUrl = root?.dataset.clearSolrUrl;
const clearQdrantUrl = root?.dataset.clearQdrantUrl;
const clearSqliteUrl = root?.dataset.clearSqliteUrl;
const clearAllUrl = root?.dataset.clearAllUrl;
const socketIoPath = root?.dataset.socketIoPath || '/socket.io';

const form = document.getElementById('indexingForm');
const stopBtn = document.getElementById('stopBtn');
const runAllBtn = document.getElementById('runAllBtn');
const workerState = document.getElementById('workerState');
const activeJob = document.getElementById('activeJob');
const jobsList = document.getElementById('jobsList');
const filesList = document.getElementById('filesList');
const sourceSelect = document.getElementById('sourceSelect');
const reloadSourcesBtn = document.getElementById('reloadSourcesBtn');
const saveSourceBtn = document.getElementById('saveSourceBtn');
const runAllCard = document.getElementById('runAllCard');
const runAllProgress = document.getElementById('runAllProgress');
const runAllLog = document.getElementById('runAllLog');

let allSources = [];

function esc(v) {
  return String(v ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    });
  } catch {
    return iso;
  }
}

const STATUS_ICON = { completed: '✓', running: '⟳', scanning: '⟳', failed: '✗', cancelled: '⊘', queued: '…' };

function formatJob(job) {
  const icon = STATUS_ICON[job.status] ?? '?';
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

  const progress = queued > 0
    ? `<div class="job-progress-bar"><div class="job-progress-fill" style="width:${Math.round(processed / queued * 100)}%"></div></div>`
    : '';

  const metaLines = [
    `<span>📁 ${esc(job.root_path)}</span>`,
    job.current_file ? `<span>🔍 ${esc(job.current_file)}</span>` : '',
    job.last_error ? `<span class="error-text">⚠ ${esc(job.last_error)}</span>` : '',
    job.started_at ? `<span>▶ Started: ${esc(fmtDate(job.started_at))}</span>` : '',
    job.finished_at ? `<span>⏹ Finished: ${esc(fmtDate(job.finished_at))}</span>` : '',
  ].filter(Boolean).join('');

  return `<div class="indexing-item">
    <div class="job-header"><span class="job-id">#${job.id}</span>${statusBadge}</div>
    ${statsHtml}
    ${progress}
    <div class="job-meta">${metaLines}</div>
  </div>`;
}

function formatFile(row) {
  const statusIcon = row.last_status === 'done' ? '✅' : '❌';
  const info = `${esc(row.repository || '-')} | ${statusIcon} ${esc(row.last_status)} | ${esc(fmtDate(row.last_indexed_at))}`;
  const error = row.last_error ? `<div class="error-text">⚠ ${esc(row.last_error)}</div>` : '';
  return `<div class="indexing-item"><strong>${esc(row.rel_path || row.file_path)}</strong><div class="muted">${info}</div>${error}</div>`;
}

async function loadStatus() {
  try {
    const res = await fetch(statusUrl, { cache: 'no-store' });
    const data = await res.json();
    renderStatus(data);
  } catch (err) {
    workerState.textContent = `Status error: ${err}`;
  }
}

function renderStatus(data) {
  workerState.textContent = data.worker_running ? 'Running' : 'Stopped';

  if (data.active_job) {
      activeJob.innerHTML = formatJob(data.active_job);
    } else {
      activeJob.textContent = 'No active job';
    }

    jobsList.innerHTML = data.recent_jobs.length
      ? data.recent_jobs.map(formatJob).join('')
      : '<div class="muted">No jobs yet.</div>';

    filesList.innerHTML = data.recent_files.length
      ? data.recent_files.map(formatFile).join('')
      : '<div class="muted">No indexed files yet.</div>';

    // Run-all panel
    const ra = data.run_all;
    if (ra && (ra.running || ra.log?.length > 0)) {
      runAllCard.style.display = '';
      const pct = ra.total > 0 ? Math.round(ra.done / ra.total * 100) : 0;
      runAllProgress.innerHTML = ra.running
        ? `<div class="run-all-status running">⟳ Running… ${ra.done}/${ra.total} sources <span class="run-all-pct">${pct}%</span></div>
           <div class="job-progress-bar"><div class="job-progress-fill" style="width:${pct}%"></div></div>`
        : `<div class="run-all-status done">✓ Done — ${ra.done}/${ra.total} sources processed</div>`;
      runAllLog.innerHTML = (ra.log || []).map(
        line => `<div class="run-all-line">${esc(line)}</div>`
      ).join('');
      // Auto-scroll log to bottom
      runAllLog.scrollTop = runAllLog.scrollHeight;

      // Disable/enable Run All button
      if (runAllBtn) runAllBtn.disabled = ra.running;
    } else {
      if (runAllBtn) runAllBtn.disabled = false;
    }
}

function getSourceLabel(source) {
  const name = source.name || source.repository || source.repository_path || source.root_path;
  const type = source.is_default ? 'default' : 'custom';
  return `${name} (${type})`;
}

function fillFormFromSource(source) {
  if (!form || !source) return;
  form.elements.root_path.value = source.root_path || '';
  form.elements.name.value = source.name || source.repository || source.repository_path || '';
  form.elements.repository_url_base.value = source.repository_url_base || '';
  const mode = source.index_mode || 'both';
  const radio = form.querySelector(`input[name="index_mode"][value="${mode}"]`);
  if (radio) radio.checked = true;
}

async function loadSources() {
  if (!sourcesUrl || !sourceSelect) {
    return;
  }
  const res = await fetch(sourcesUrl, { cache: 'no-store' });
  const data = await res.json();
  allSources = Array.isArray(data.sources) ? data.sources : [];

  sourceSelect.innerHTML = '<option value="">-- Select a source --</option>';
  allSources.forEach((source, idx) => {
    const opt = document.createElement('option');
    opt.value = String(idx);
    opt.textContent = getSourceLabel(source);
    sourceSelect.appendChild(opt);
  });
}

form?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(form).entries());

  const res = await fetch(startUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || 'Failed to start indexing job.');
    return;
  }

  await loadStatus();
});

stopBtn?.addEventListener('click', async () => {
  const res = await fetch(stopUrl, { method: 'POST' });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || 'Failed to stop job.');
    return;
  }
  await loadStatus();
});

runAllBtn?.addEventListener('click', async () => {
  if (!runAllUrl) return;
  if (!confirm(`Run git pull / svn update on all sources, then queue all for indexing?`)) return;
  runAllBtn.disabled = true;
  if (runAllCard) runAllCard.style.display = '';
  if (runAllProgress) runAllProgress.innerHTML = '<div class="run-all-status running">⟳ Starting…</div>';
  if (runAllLog) runAllLog.innerHTML = '';
  const res = await fetch(runAllUrl, { method: 'POST' });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || 'Failed to start run-all.');
    runAllBtn.disabled = false;
    return;
  }
  await loadStatus();
});

sourceSelect?.addEventListener('change', () => {
  const idx = Number(sourceSelect.value);
  if (!Number.isInteger(idx) || idx < 0 || idx >= allSources.length) {
    return;
  }
  fillFormFromSource(allSources[idx]);
});

reloadSourcesBtn?.addEventListener('click', async () => {
  try {
    await loadSources();
  } catch (err) {
    alert(`Failed to load sources: ${err}`);
  }
});

saveSourceBtn?.addEventListener('click', async () => {
  if (!form) {
    return;
  }
  const payload = Object.fromEntries(new FormData(form).entries());
  if (!payload.name?.trim()) {
    alert('Please enter Source Name before adding repository.');
    return;
  }

  const res = await fetch(addSourceUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || 'Failed to add repository source.');
    return;
  }

  await loadSources();
  alert('Repository source added successfully.');
});

loadStatus();
loadSources();

// ── Clear Data ──
async function doClear(url, label) {
  const clearStatus = document.getElementById('clearStatus');
  if (!url) return;
  if (!confirm(`This will permanently delete all ${label} data. Continue?`)) return;
  if (clearStatus) clearStatus.textContent = `Clearing ${label}…`;
  try {
    const res = await fetch(url, { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      const msg = data.errors ? data.errors.join('; ') : (data.error || 'Unknown error');
      if (clearStatus) clearStatus.textContent = `Error: ${msg}`;
      alert(`Failed to clear ${label}: ${msg}`);
      return;
    }
    if (clearStatus) clearStatus.textContent = data.message || `${label} cleared.`;
    await loadStatus();
  } catch (err) {
    if (clearStatus) clearStatus.textContent = `Error: ${err}`;
    alert(`Failed to clear ${label}: ${err}`);
  }
}

document.getElementById('clearSolrBtn')?.addEventListener('click', () => doClear(clearSolrUrl, 'Solr'));
document.getElementById('clearQdrantBtn')?.addEventListener('click', () => doClear(clearQdrantUrl, 'Qdrant'));
document.getElementById('clearSqliteBtn')?.addEventListener('click', () => doClear(clearSqliteUrl, 'SQLite'));
document.getElementById('clearAllBtn')?.addEventListener('click', () => doClear(clearAllUrl, 'All (Solr + Qdrant + SQLite)'));

// ── Socket.IO ──
const socket = io({ path: socketIoPath, transports: ['websocket', 'polling'] });
socket.on('indexing_status', (data) => renderStatus(data));
socket.on('connect_error', () => {
  // Fall back to polling when socket is unavailable
  if (!window._statusPollFallback) {
    window._statusPollFallback = setInterval(loadStatus, 5000);
  }
});
socket.on('connect', () => {
  if (window._statusPollFallback) {
    clearInterval(window._statusPollFallback);
    window._statusPollFallback = null;
  }
});
