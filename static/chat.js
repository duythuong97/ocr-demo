/* chat.js — RAG Chat frontend */
(() => {
  "use strict";

  // ── Markdown renderer ─────────────────────────────────────────────────────
  if (typeof marked !== "undefined") {
    marked.setOptions({
      gfm: true,
      breaks: true,
      highlight:
        typeof hljs !== "undefined"
          ? (code, lang) => {
              const language = hljs.getLanguage(lang) ? lang : "plaintext";
              return hljs.highlight(code, { language }).value;
            }
          : null,
    });
  }

  function renderMarkdown(text) {
    return typeof marked !== "undefined" ? marked.parse(text) : escHtml(text);
  }

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const root = document.getElementById("chatRoot");
  const CHAT_URL = root?.dataset.chatUrl || "/api/chat";
  const STATUS_URL = root?.dataset.statusUrl || "/api/chat/status";
  const HISTORY_URL = root?.dataset.historyUrl || "/api/chat/history";
  const SESSIONS_URL = root?.dataset.sessionsUrl || "/api/chat/sessions";
  const messagesEl = document.getElementById("chatMessages");
  const welcomeEl = document.getElementById("chatWelcome");
  const textarea = document.getElementById("chatInput");
  const sendBtn = document.getElementById("sendBtn");
  const topKInput = document.getElementById("topKInput");
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("lmStatusText");
  const clearBtn = document.getElementById("clearBtn");
  const newChatBtn = document.getElementById("newChatBtn");
  const sessionsList = document.getElementById("sessionsList");

  // Sidebar
  const sidebar = document.getElementById("contextSidebar");
  const sidebarToggle = document.getElementById("sidebarToggleBtn");
  const tabBtnSources = document.getElementById("tabBtnSources");
  const tabBtnGraph = document.getElementById("tabBtnGraph");
  const panelSources = document.getElementById("panelSources");
  const panelGraph = document.getElementById("panelGraph");
  const sourcesCount = document.getElementById("sourcesCount");
  const graphCount = document.getElementById("graphCount");

  // ── State ─────────────────────────────────────────────────────────────────
  let history = [];
  let busy = false;
  let activeTab = "sources";
  let _currentTrace = null; // live trace for in-flight query
  const _msgCtx = new WeakMap(); // bodyEl → {sources, graph}

  // ── Session & server-side history ────────────────────────────────────────
  const LS_SESSION_KEY = "rag_session_id";

  function _uuid() {
    if (typeof crypto !== "undefined" && crypto.randomUUID)
      return crypto.randomUUID();
    // Fallback for HTTP (non-secure) contexts where randomUUID is unavailable
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = crypto.getRandomValues(new Uint8Array(1))[0] & 15;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function _getOrCreateSessionId() {
    let sid = "";
    try {
      sid = localStorage.getItem(LS_SESSION_KEY) || "";
    } catch {
      /**/
    }
    if (
      !sid ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        sid,
      )
    ) {
      sid = _uuid();
      try {
        localStorage.setItem(LS_SESSION_KEY, sid);
      } catch {
        /**/
      }
    }
    return sid;
  }
  const SESSION_ID = _getOrCreateSessionId();

  // Common headers for /api/chat* requests
  const _apiHeaders = () => ({
    "Content-Type": "application/json",
    "X-Session-ID": SESSION_ID,
  });

  async function _saveMessage(
    role,
    content,
    events = [],
    sources = [],
    graph = {},
  ) {
    try {
      await fetch(HISTORY_URL, {
        method: "POST",
        headers: _apiHeaders(),
        body: JSON.stringify({
          session_id: SESSION_ID,
          role,
          content,
          events,
          sources,
          graph,
        }),
      });
    } catch {
      /**/
    }
  }

  async function _deleteHistory() {
    try {
      await fetch(
        `${HISTORY_URL}?session_id=${encodeURIComponent(SESSION_ID)}`,
        {
          method: "DELETE",
          headers: { "X-Session-ID": SESSION_ID },
        },
      );
    } catch {
      /**/
    }
  }

  async function restoreChat() {
    try {
      const res = await fetch(
        `${HISTORY_URL}?session_id=${encodeURIComponent(SESSION_ID)}`,
        { headers: { "X-Session-ID": SESSION_ID } },
      );
      if (!res.ok) return;
      const data = await res.json();
      const messages = data.messages || [];
      if (!messages.length) return;
      if (welcomeEl) welcomeEl.style.display = "none";
      let lastBodyEl = null;
      for (const msg of messages) {
        const { bodyEl, bubbleEl } = appendMessage(msg.role, msg.content);
        history.push({ role: msg.role, content: msg.content });
        if (msg.role === "assistant") {
          // Render a static (collapsed) agent trace if events were saved
          if (msg.events && msg.events.length) {
            const totalDocs = (msg.sources || []).length;
            const traceEl = _renderStaticAgentTrace(msg.events, totalDocs);
            bodyEl.insertBefore(traceEl, bubbleEl);
          }
          _attachMsgClick(bodyEl, msg.sources || [], msg.graph || {});
          lastBodyEl = bodyEl;
        }
      }
      // Restore sidebar to last assistant message + mark it active
      if (lastBodyEl) {
        const ctx = _msgCtx.get(lastBodyEl);
        if (ctx) {
          _setActiveMsg(lastBodyEl);
          updateSidebar(ctx.sources, ctx.graph);
        }
      }
    } catch {
      /**/
    }
  }
  restoreChat();

  // ── Proxy health ──────────────────────────────────────────────────────────
  async function checkStatus() {
    try {
      const res = await fetch(STATUS_URL);
      if (!res.ok) throw new Error();
      const data = await res.json();
      statusDot.className =
        "status-dot " + (data.available ? "online" : "offline");
      statusText.textContent = data.available
        ? "Proxy online"
        : "Proxy unreachable";
    } catch {
      statusDot.className = "status-dot offline";
      statusText.textContent = "Proxy unreachable";
    }
  }
  checkStatus();
  setInterval(checkStatus, 300_000); // every 5 minutes

  // ── Textarea auto-resize ──────────────────────────────────────────────────
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
  });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy) send();
    }
  });
  sendBtn.addEventListener("click", () => {
    if (!busy) send();
  });
  clearBtn.addEventListener("click", clearChat);

  // ── Sidebar toggle ────────────────────────────────────────────────────────
  sidebarToggle?.addEventListener("click", () =>
    sidebar?.classList.toggle("collapsed"),
  );

  // ── Tab switching ─────────────────────────────────────────────────────────
  function switchTab(tab) {
    activeTab = tab;
    [tabBtnSources, tabBtnGraph].forEach((btn) =>
      btn?.classList.toggle("active", btn?.dataset.tab === tab),
    );
    panelSources?.classList.toggle("active", tab === "sources");
    panelGraph?.classList.toggle("active", tab === "graph");
  }
  tabBtnSources?.addEventListener("click", () => switchTab("sources"));
  tabBtnGraph?.addEventListener("click", () => switchTab("graph"));

  // ── Suggestion chips ──────────────────────────────────────────────────────
  document.querySelectorAll(".suggestion-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      textarea.value = chip.textContent.trim();
      textarea.dispatchEvent(new Event("input"));
      send();
    });
  });

  // ── Clear ─────────────────────────────────────────────────────────────────
  async function clearChat() {
    history = [];
    await _deleteHistory();
    messagesEl.innerHTML = "";
    welcomeEl.style.display = "flex";
    clearSidebar();
    _loadSessions();
  }

  function clearSidebar() {
    if (sourcesCount) sourcesCount.textContent = "";
    if (graphCount) graphCount.textContent = "";
    if (panelSources)
      panelSources.innerHTML = `<div class="sidebar-hint">Send a message to see retrieved sources.</div>`;
    if (panelGraph)
      panelGraph.innerHTML = `<div class="sidebar-hint">Send a message to see graph context.</div>`;
  }

  // ── Sessions sidebar ──────────────────────────────────────────────────────
  function _relativeTime(isoStr) {
    if (!isoStr) return "";
    const diff = Date.now() - new Date(isoStr).getTime();
    const mins = Math.floor(diff / 60_000);
    const hours = Math.floor(diff / 3_600_000);
    const days = Math.floor(diff / 86_400_000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7) return `${days}d ago`;
    return new Date(isoStr).toLocaleDateString();
  }

  async function _loadSessions() {
    if (!sessionsList) return;
    try {
      const res = await fetch(SESSIONS_URL);
      if (!res.ok) throw new Error("failed");
      const data = await res.json();
      const sessions = data.sessions || [];
      if (!sessions.length) {
        sessionsList.innerHTML = `<div class="sessions-loading">No history yet</div>`;
        return;
      }
      sessionsList.innerHTML = sessions
        .map((s) => {
          const isActive = s.id === SESSION_ID;
          return `<div class="session-item${isActive ? " active" : ""}" data-sid="${escHtml(s.id)}" title="${escHtml(s.title)}">
          <div class="session-item-row">
            <span class="session-title">${escHtml(s.title)}</span>
            <button class="session-delete-btn" data-sid="${escHtml(s.id)}" title="Delete chat">✕</button>
          </div>
          <span class="session-meta">${_relativeTime(s.last_active_at)}</span>
        </div>`;
        })
        .join("");
      sessionsList.querySelectorAll(".session-item").forEach((el) => {
        el.addEventListener("click", () => _switchSession(el.dataset.sid));
      });
      sessionsList.querySelectorAll(".session-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          _deleteSession(btn.dataset.sid);
        });
      });
    } catch {
      if (sessionsList)
        sessionsList.innerHTML = `<div class="sessions-loading">—</div>`;
    }
  }

  async function _deleteSession(sid) {
    try {
      await fetch(`${HISTORY_URL}?session_id=${encodeURIComponent(sid)}`, {
        method: "DELETE",
        headers: { "X-Session-ID": SESSION_ID },
      });
    } catch {
      /**/
    }
    if (sid === SESSION_ID) {
      // Deleted current session → start fresh
      const newSid = _uuid();
      try {
        localStorage.setItem(LS_SESSION_KEY, newSid);
      } catch {
        /**/
      }
      window.location.reload();
    } else {
      _loadSessions();
    }
  }

  function _switchSession(sid) {
    if (sid === SESSION_ID) return; // already active
    try {
      localStorage.setItem(LS_SESSION_KEY, sid);
    } catch {
      /**/
    }
    window.location.reload();
  }

  function _startNewSession() {
    const sid = _uuid();
    try {
      localStorage.setItem(LS_SESSION_KEY, sid);
    } catch {
      /**/
    }
    window.location.reload();
  }

  if (newChatBtn) newChatBtn.addEventListener("click", _startNewSession);
  _loadSessions();

  // ── Per-message context (click to view) ───────────────────────────────────
  function _setActiveMsg(bodyEl) {
    // Remove active from all, set on clicked
    messagesEl
      .querySelectorAll(".message-body.ctx-active")
      .forEach((el) => el.classList.remove("ctx-active"));
    bodyEl.classList.add("ctx-active");
  }

  function _attachMsgClick(bodyEl, sources, graph) {
    const hasSrc = sources && sources.length > 0;
    const hasGraph =
      graph && Array.isArray(graph.nodes) && graph.nodes.length > 0;
    if (!hasSrc && !hasGraph) return; // nothing to show

    _msgCtx.set(bodyEl, { sources: sources || [], graph: graph || {} });
    bodyEl.classList.add("has-context");

    bodyEl.addEventListener("click", (e) => {
      if (e.target.closest("a")) return; // don't intercept links
      const ctx = _msgCtx.get(bodyEl);
      if (!ctx) return;
      _setActiveMsg(bodyEl);
      updateSidebar(ctx.sources, ctx.graph);
      if (ctx.sources.length > 0) switchTab("sources");
      else if ((ctx.graph?.nodes || []).length) switchTab("graph");
    });
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  async function send() {
    const text = textarea.value.trim();
    if (!text) return;
    setBusy(true);

    if (welcomeEl) welcomeEl.style.display = "none";
    appendMessage("user", text);
    history.push({ role: "user", content: text });
    _saveMessage("user", text); // fire-and-forget: persist before stream starts
    textarea.value = "";
    textarea.style.height = "auto";

    // Create assistant message first, then attach trace INSIDE its body above the bubble
    const { bubbleEl, bodyEl } = appendMessage("assistant", "");
    _currentTrace = _createAgentTrace();
    bodyEl.insertBefore(_currentTrace.el, bubbleEl);
    bubbleEl.classList.add("streaming");

    let fullAnswer = "";
    let hasTools = false;
    let _pendingEvents = []; // tool_call / tool_result events to persist
    let _pendingSources = []; // docs to persist
    let _pendingGraph = {}; // graph to persist
    let _pendingThinkingText = ""; // accumulates thinking_token chars for history

    try {
      const res = await fetch(CHAT_URL, {
        method: "POST",
        headers: _apiHeaders(),
        body: JSON.stringify({
          message: text,
          history: history.slice(0, -1),
          search_mode: "semantic",
          top_k: parseInt(topKInput.value, 10) || 5,
          stream: true,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || res.statusText);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;
          try {
            const evt = JSON.parse(raw);
            if (evt.type === "thinking_token") {
              _appendAgentThinkingToken(evt.text);
              _pendingThinkingText += evt.text;
            } else if (evt.type === "thinking") {
              _addAgentThinking(evt.text);
              _pendingEvents.push(evt);
            } else if (evt.type === "tool_call") {
              hasTools = true;
              _addAgentStep(evt.id, evt.name, evt.args);
              _pendingEvents.push(evt);
            } else if (evt.type === "tool_result") {
              _resolveAgentStep(evt.id, evt.count, evt.error);
              _pendingEvents.push(evt);
            } else if (evt.type === "context") {
              const docs = evt.docs || [];
              const graph = evt.graph || {};
              updateSidebar(docs, graph);
              _finalizeAgentTrace(docs.length);
              if (docs.length > 0) switchTab("sources");
              _pendingSources = docs;
              _pendingGraph = graph;
            } else if (evt.type === "token") {
              fullAnswer += evt.text;
              bubbleEl.innerHTML = renderMarkdown(fullAnswer);
              scrollBottom();
            } else if (evt.type === "error") {
              throw new Error(evt.text);
            }
          } catch (parseErr) {
            if (parseErr.message && !parseErr.message.includes("JSON"))
              throw parseErr;
          }
        }
      }
    } catch (err) {
      if (_currentTrace) _currentTrace.el.style.display = "none";
      bubbleEl.innerHTML = `<span class="error-banner">Error: ${escHtml(err.message)}</span>`;
    } finally {
      bubbleEl.classList.remove("streaming");
      if (!hasTools && _currentTrace) _currentTrace.el.style.display = "none";
      _currentTrace = null;
      setBusy(false);
    }

    // Persist assistant message (user message was already saved above before the stream)
    if (fullAnswer) {
      // Synthesize a "thinking" event for history restore (from streaming tokens)
      if (_pendingThinkingText) {
        _pendingEvents.unshift({
          type: "thinking",
          text: _pendingThinkingText,
        });
      }
      history.push({ role: "assistant", content: fullAnswer });
      await _saveMessage(
        "assistant",
        fullAnswer,
        _pendingEvents,
        _pendingSources,
        _pendingGraph,
      );
      _attachMsgClick(bodyEl, _pendingSources, _pendingGraph);
      _setActiveMsg(bodyEl);
      _loadSessions(); // refresh sessions list to update title + recency
    }
    scrollBottom();
  }

  // ── Agent Trace helpers ────────────────────────────────────────────────────────
  const TOOL_ICONS = {
    search_documents: "🔍",
    get_graph_neighbors: "🕸️",
  };

  function _createAgentTrace() {
    const el = document.createElement("div");
    el.className = "agent-trace";
    el.innerHTML =
      `<div class="agent-trace-header">` +
      `<span class="agent-trace-spinner"></span>` +
      `<span class="agent-trace-label">Thinking…</span>` +
      `<span class="agent-trace-chevron">▾</span>` +
      `</div>` +
      `<div class="agent-trace-body">` +
      `<div class="agent-trace-steps"></div>` +
      `<div class="agent-trace-summary" style="display:none"></div>` +
      `</div>`;
    el.querySelector(".agent-trace-header").addEventListener("click", () =>
      el.classList.toggle("collapsed"),
    );
    return {
      el,
      stepsEl: el.querySelector(".agent-trace-steps"),
      summaryEl: el.querySelector(".agent-trace-summary"),
      headerLbl: el.querySelector(".agent-trace-label"),
      headerSpinner: el.querySelector(".agent-trace-spinner"),
      stepsMap: {},
      totalDocs: 0,
    };
  }

  function _addAgentThinking(text) {
    if (!_currentTrace || !text) return;
    // One thinking block per trace — replace if already exists
    let thinkEl = _currentTrace.stepsEl.querySelector(".agent-thinking");
    if (!thinkEl) {
      thinkEl = document.createElement("div");
      thinkEl.className = "agent-thinking";
      _currentTrace.stepsEl.insertBefore(
        thinkEl,
        _currentTrace.stepsEl.firstChild,
      );
    }
    thinkEl.textContent = text;
    scrollBottom();
  }

  // Streaming variant: appends one token at a time (typewriter effect)
  function _appendAgentThinkingToken(token) {
    if (!_currentTrace || !token) return;
    let thinkEl = _currentTrace.stepsEl.querySelector(".agent-thinking");
    if (!thinkEl) {
      thinkEl = document.createElement("div");
      thinkEl.className = "agent-thinking";
      _currentTrace.stepsEl.insertBefore(
        thinkEl,
        _currentTrace.stepsEl.firstChild,
      );
    }
    thinkEl.textContent += token;
    scrollBottom();
  }

  const TOOL_LABELS = {
    search_documents: "Searching knowledge base…",
    get_graph_neighbors: "Querying knowledge graph…",
  };

  function _addAgentStep(id, name, argsJson) {
    if (!_currentTrace) return;
    // Update header label to reflect the actual tool being invoked
    if (_currentTrace.headerLbl) {
      _currentTrace.headerLbl.textContent = TOOL_LABELS[name] || "Running tool…";
    }
    const icon = TOOL_ICONS[name] || "🔧";
    let query = "";
    try {
      const p = JSON.parse(argsJson);
      query = p.query || p.qname || String(Object.values(p)[0] ?? "");
    } catch {
      /**/
    }
    const stepEl = document.createElement("div");
    stepEl.className = "agent-step";
    stepEl.innerHTML =
      `<span class="agent-step-icon">${icon}</span>` +
      `<span class="agent-step-name">${escHtml(name)}</span>` +
      `<span class="agent-step-query">${query ? `"${escHtml(String(query).slice(0, 55))}"` : ""}</span>` +
      `<span class="agent-step-status"><span class="step-spinner"></span></span>`;
    _currentTrace.stepsEl.appendChild(stepEl);
    _currentTrace.stepsMap[id] = stepEl;
    scrollBottom();
  }

  function _resolveAgentStep(id, count, error) {
    if (!_currentTrace) return;
    const stepEl = _currentTrace.stepsMap[id];
    if (!stepEl) return;
    const statusEl = stepEl.querySelector(".agent-step-status");
    if (!statusEl) return;
    if (error) {
      statusEl.innerHTML = `<span class="agent-step-badge err">error</span>`;
    } else if (count > 0) {
      statusEl.innerHTML = `<span class="agent-step-badge ok">${count} doc${count !== 1 ? "s" : ""}</span>`;
      _currentTrace.totalDocs += count;
    } else {
      statusEl.innerHTML = `<span class="agent-step-badge zero">0 docs</span>`;
    }
  }

  function _finalizeAgentTrace(totalDocs) {
    if (!_currentTrace) return;
    const spinner = _currentTrace.headerSpinner;
    if (spinner) {
      spinner.style.animation = "none";
      spinner.style.opacity = "0";
    }
    const nTools = Object.keys(_currentTrace.stepsMap).length;
    _currentTrace.headerLbl.textContent = `Found ${totalDocs} doc${totalDocs !== 1 ? "s" : ""} via ${nTools} tool call${nTools !== 1 ? "s" : ""}`;
    _currentTrace.summaryEl.style.display = "flex";
    _currentTrace.summaryEl.textContent = `${nTools} call${nTools !== 1 ? "s" : ""} · ${totalDocs} document${totalDocs !== 1 ? "s" : ""} retrieved`;
    setTimeout(() => {
      if (_currentTrace) _currentTrace.el.classList.add("collapsed");
    }, 1800);
  }

  // Build a static (collapsed) agent trace element from persisted events (history restore)
  function _renderStaticAgentTrace(events, totalDocs) {
    const toolCalls = events.filter((e) => e.type === "tool_call");
    const toolResults = {};
    events
      .filter((e) => e.type === "tool_result")
      .forEach((e) => {
        toolResults[e.id] = e;
      });

    const nTools = toolCalls.length;
    const summary = `${nTools} call${nTools !== 1 ? "s" : ""} · ${totalDocs} document${totalDocs !== 1 ? "s" : ""} retrieved`;

    let stepsHtml = "";
    for (const tc of toolCalls) {
      const icon = TOOL_ICONS[tc.name] || "🔧";
      let query = "";
      try {
        const p = JSON.parse(tc.args || "{}");
        query =
          p.query || p.qualified_name || String(Object.values(p)[0] ?? "");
      } catch {
        /**/
      }
      const res = toolResults[tc.id];
      let badgeHtml = "";
      if (res) {
        if (res.error)
          badgeHtml = `<span class="agent-step-badge err">error</span>`;
        else if (res.count > 0)
          badgeHtml = `<span class="agent-step-badge ok">${res.count} doc${res.count !== 1 ? "s" : ""}</span>`;
        else badgeHtml = `<span class="agent-step-badge zero">0 docs</span>`;
      }
      stepsHtml += `<div class="agent-step">
        <span class="agent-step-icon">${icon}</span>
        <span class="agent-step-name">${escHtml(tc.name)}</span>
        <span class="agent-step-query">${query ? `"${escHtml(String(query).slice(0, 55))}"` : ""}</span>
        <span class="agent-step-status">${badgeHtml}</span>
      </div>`;
    }

    const el = document.createElement("div");
    el.className = "agent-trace collapsed";
    el.innerHTML =
      `<div class="agent-trace-header">` +
      `<span class="agent-trace-label">Found ${totalDocs} doc${totalDocs !== 1 ? "s" : ""} via ${nTools} tool call${nTools !== 1 ? "s" : ""}</span>` +
      `<span class="agent-trace-chevron">▾</span>` +
      `</div>` +
      `<div class="agent-trace-body">` +
      `<div class="agent-trace-steps">${stepsHtml}</div>` +
      `<div class="agent-trace-summary">${escHtml(summary)}</div>` +
      `</div>`;
    el.querySelector(".agent-trace-header").addEventListener("click", () =>
      el.classList.toggle("collapsed"),
    );
    return el;
  }

  // ── Sidebar update ────────────────────────────────────────────────────────
  function updateSidebar(docs, graph) {
    const hasGraph =
      graph && Array.isArray(graph.nodes) && graph.nodes.length > 0;

    if (sourcesCount)
      sourcesCount.textContent = docs.length ? String(docs.length) : "";
    if (graphCount)
      graphCount.textContent = hasGraph ? String(graph.nodes.length) : "";

    if (panelSources) {
      panelSources.innerHTML = docs.length
        ? renderSourcesPanel(docs)
        : `<div class="sidebar-hint">No document sources retrieved.</div>`;
    }
    if (panelGraph) {
      panelGraph.innerHTML = hasGraph
        ? renderGraphPanel(graph)
        : `<div class="sidebar-hint">No graph data for these files.</div>`;
      if (hasGraph) _mountVisNetwork(graph);
    }

    // Auto-switch: graph-only → show graph, docs available → send() handles it
    if (hasGraph && !docs.length) switchTab("graph");
  }

  // ── Sources panel ─────────────────────────────────────────────────────────
  function renderSourcesPanel(docs) {
    return docs
      .map((doc, idx) => {
        const score =
          doc.score != null ? (doc.score * 100).toFixed(1) + "%" : "—";
        const filePath = doc.file_path || doc.rel_path || doc.file || "unknown";
        const fileName = filePath.split("/").pop() || filePath;
        const dirPart = filePath.includes("/")
          ? filePath.substring(0, filePath.lastIndexOf("/"))
          : "";
        const repo = doc.repository || "";
        const fileType =
          doc.file_type ||
          (fileName.includes(".") ? fileName.split(".").pop() : "");
        const preview = (doc.text || "").trim();

        return `
        <div class="context-doc">
          <div class="context-doc-header">
            <span class="context-doc-index">#${idx + 1}</span>
            <span class="context-doc-score ${parseFloat(doc.score || 0) >= 0.7 ? "score-high" : parseFloat(doc.score || 0) >= 0.5 ? "score-mid" : "score-low"}">${score}</span>
          </div>
          <div class="context-doc-meta">
            ${repo ? `<span class="meta-tag meta-repo" title="Repository">${escHtml(repo)}</span>` : ""}
            ${fileType ? `<span class="meta-tag meta-type">${escHtml(fileType)}</span>` : ""}
          </div>
          <div class="context-doc-path" title="${escHtml(filePath)}">
            ${dirPart ? `<span class="path-dir">${escHtml(dirPart)}/</span>` : ""}
            <span class="path-file">${escHtml(fileName)}</span>
          </div>
          ${preview ? `<pre class="context-doc-text">${escHtml(preview)}</pre>` : ""}
        </div>`;
      })
      .join("");
  }

  // ── Graph panel (vis-network 9.x) ────────────────────────────────────────
  // Neo4j Browser-style palette
  const LABEL_COLOR = {
    Table: {
      background: "#4C8EDA",
      border: "#3577C4",
      hover: { background: "#5E9EEC", border: "#3577C4" },
      highlight: { background: "#6AAEF5", border: "#3577C4" },
    },
    Function: {
      background: "#D4A843",
      border: "#B88A28",
      hover: { background: "#E6BB55", border: "#B88A28" },
      highlight: { background: "#F0C060", border: "#B88A28" },
    },
    Class: {
      background: "#6DCE9E",
      border: "#44A877",
      hover: { background: "#82DFB0", border: "#44A877" },
      highlight: { background: "#8FDFB8", border: "#44A877" },
    },
    ApiEndpoint: {
      background: "#E8735A",
      border: "#C45030",
      hover: { background: "#F2876E", border: "#C45030" },
      highlight: { background: "#F5957E", border: "#C45030" },
    },
    CronJob: {
      background: "#A78BFA",
      border: "#7C5BD0",
      hover: { background: "#BAA3FC", border: "#7C5BD0" },
      highlight: { background: "#C4B0FF", border: "#7C5BD0" },
    },
    Service: {
      background: "#F97316",
      border: "#CA5B08",
      hover: { background: "#FB8A38", border: "#CA5B08" },
      highlight: { background: "#FCA44A", border: "#CA5B08" },
    },
    Domain: {
      background: "#22D3EE",
      border: "#0A9DB5",
      hover: { background: "#38DDFA", border: "#0A9DB5" },
      highlight: { background: "#67E8F9", border: "#0A9DB5" },
    },
    Repository: {
      background: "#778CA3",
      border: "#556070",
      hover: { background: "#8FA0B5", border: "#556070" },
      highlight: { background: "#A0B2C4", border: "#556070" },
    },
    EventTopic: {
      background: "#EC4899",
      border: "#BE185D",
      hover: { background: "#F472B6", border: "#BE185D" },
      highlight: { background: "#F9A8D4", border: "#BE185D" },
    },
    Module: {
      background: "#10B981",
      border: "#047857",
      hover: { background: "#34D399", border: "#047857" },
      highlight: { background: "#6EE7B7", border: "#047857" },
    },
    File: {
      background: "#64748B",
      border: "#334155",
      hover: { background: "#7E8FA3", border: "#334155" },
      highlight: { background: "#94A3B8", border: "#334155" },
    },
    ExternalService: {
      background: "#EF4444",
      border: "#B91C1C",
      hover: { background: "#F87171", border: "#B91C1C" },
      highlight: { background: "#FCA5A5", border: "#B91C1C" },
    },
    FrontendPage: {
      background: "#8B5CF6",
      border: "#6D28D9",
      hover: { background: "#A78BFA", border: "#6D28D9" },
      highlight: { background: "#C4B5FD", border: "#6D28D9" },
    },
    FrontendComponent: {
      background: "#7C3AED",
      border: "#5B21B6",
      hover: { background: "#8B5CF6", border: "#5B21B6" },
      highlight: { background: "#A78BFA", border: "#5B21B6" },
    },
    ApiGateway: {
      background: "#0EA5E9",
      border: "#0369A1",
      hover: { background: "#38BDF8", border: "#0369A1" },
      highlight: { background: "#7DD3FC", border: "#0369A1" },
    },
  };
  const DEFAULT_COLOR = {
    background: "#697A8D",
    border: "#4A5568",
    hover: { background: "#7E8FA3", border: "#4A5568" },
    highlight: { background: "#9BB0C9", border: "#4A5568" },
  };

  const REL_COLOR = {
    WRITES_TO: "#E8735A",
    READS_FROM: "#6DCE9E",
    BELONGS_TO: "#778CA3",
    CALLS: "#F97316",
    EXTENDS: "#A78BFA",
    IMPLEMENTS: "#22D3EE",
    USES: "#D4A843",
    HAS: "#4C8EDA",
    HANDLED_BY: "#E8735A",
    TRIGGERS: "#A78BFA",
    CALLS_API: "#F97316",
    CALLS_EXTERNAL: "#EF4444",
    PUBLISHES: "#EC4899",
    SUBSCRIBES: "#8B5CF6",
    DEFINED_IN: "#64748B",
    IMPORTS: "#10B981",
    INSTANTIATES: "#0EA5E9",
  };
  const DEFAULT_REL = "#4A5568";

  let _visNet = null;

  function _nodePrimaryName(n) {
    const p = n.props || {};
    // Always take the last segment after ":" so "Table:dummy:EMPLOYEES" → "EMPLOYEES"
    const raw = p.name || p.qualified_name || "";
    return raw.split(":").pop() || "?";
  }

  function _nodeLabel(n) {
    const p = n.props || {};
    const lbl = n.label || "";
    const name = _nodePrimaryName(n);
    // Show a small sub-line only for meaningful subtypes
    if (lbl === "ApiEndpoint" && p.method) {
      const path = (p.path || "").replace(/\{[^}]+\}/g, "…").slice(0, 24);
      return `${p.method.toUpperCase()}\n${path}`;
    }
    if (lbl === "Function" && p.proc_type) return `${name}\n${p.proc_type}`;
    if (lbl === "Table" && p.is_view) return `${name}\n(VIEW)`;
    return name;
  }

  function renderGraphPanel(graph) {
    return `<div id="visGraphCanvas" class="vis-graph-canvas"></div>
            <div id="visGraphLegend" class="vis-graph-legend"></div>
            <div id="visNodeDetail" class="vis-node-detail" style="display:none"></div>`;
  }

  function _mountVisNetwork(graph) {
    const container = document.getElementById("visGraphCanvas");
    if (!container) return;
    if (typeof vis === "undefined") {
      container.innerHTML = `<div style="color:#94A3B8;padding:2rem;text-align:center;font-size:.8rem">vis-network not loaded</div>`;
      return;
    }

    if (_visNet) {
      _visNet.destroy();
      _visNet = null;
    }

    const nodes = graph.nodes || [];
    const edges = graph.edges || [];

    const _nodeById = {};
    const visNodes = new vis.DataSet(
      nodes.map((n, i) => {
        const col = LABEL_COLOR[n.label] || DEFAULT_COLOR;
        const id = n.props?.qualified_name || String(i);
        _nodeById[id] = n;
        const name = _nodePrimaryName(n);
        return {
          id,
          label: _nodeLabel(n),
          title: escHtml(name), // plain tooltip on hover (no html flicker)
          color: col,
          shape: "dot",
          size: 26,
          font: {
            color: "#F1F5F9",
            size: 12,
            face: "Inter, ui-sans-serif, sans-serif",
            strokeWidth: 3,
            strokeColor: "rgba(0,0,0,0.7)",
            bold: {
              size: 12,
              face: "Inter, ui-sans-serif, sans-serif",
              color: "#F1F5F9",
              mod: "bold",
            },
          },
          borderWidth: 2,
          borderWidthSelected: 3,
          shadow: {
            enabled: true,
            color: "rgba(0,0,0,0.45)",
            size: 10,
            x: 0,
            y: 3,
          },
        };
      }),
    );

    const visEdges = new vis.DataSet(
      edges.map((e, i) => {
        const c = REL_COLOR[e.rel] || DEFAULT_REL;
        const fromId =
          nodes.find((n) => n.props?.qualified_name === e.from)?.props
            ?.qualified_name ?? e.from;
        const toId =
          nodes.find((n) => n.props?.qualified_name === e.to)?.props
            ?.qualified_name ?? e.to;
        return {
          id: "e" + i,
          from: fromId,
          to: toId,
          label: e.rel || "",
          color: { color: c, highlight: c, hover: c, opacity: 0.75 },
          font: {
            color: "#94A3B8",
            size: 10,
            face: "Inter, ui-sans-serif, sans-serif",
            align: "middle",
            strokeWidth: 2,
            strokeColor: "#0D1520",
          },
          arrows: { to: { enabled: true, scaleFactor: 0.55, type: "arrow" } },
          smooth: { enabled: true, type: "continuous", roundness: 0.2 },
          width: 1.5,
          selectionWidth: 2.5,
          hoverWidth: 2,
        };
      }),
    );

    const options = {
      physics: {
        enabled: true,
        solver: "forceAtlas2Based",
        forceAtlas2Based: {
          gravitationalConstant: -55,
          centralGravity: 0.008,
          springLength: 120,
          springConstant: 0.05,
          damping: 0.5,
          avoidOverlap: 0.5,
        },
        stabilization: {
          enabled: true,
          iterations: 200,
          fit: true,
          updateInterval: 25,
        },
        minVelocity: 0.75,
      },
      interaction: {
        hover: true,
        tooltipDelay: 300,
        navigationButtons: false,
        keyboard: false,
        multiselect: false,
        zoomView: true,
        dragView: true,
      },
      layout: { improvedLayout: true, randomSeed: 42 },
      nodes: { scaling: { min: 20, max: 40 } },
      edges: { scaling: { min: 1, max: 3 } },
    };

    _visNet = new vis.Network(
      container,
      { nodes: visNodes, edges: visEdges },
      options,
    );

    // Click → detail panel
    _visNet.on("click", (params) => {
      const detail = document.getElementById("visNodeDetail");
      if (!detail) return;
      if (!params.nodes.length) {
        detail.style.display = "none";
        return;
      }

      const clickedId = params.nodes[0];
      const n = _nodeById[clickedId];
      if (!n) return;

      const p = n.props || {};
      const lbl = n.label || "Node";
      const bg = (LABEL_COLOR[lbl] || DEFAULT_COLOR).background;
      const displayName = _nodePrimaryName(n);

      // ── Properties ────────────────────────────────────────────────────
      const skip = new Set(["qualified_name"]);
      const propRows = Object.entries(p)
        .filter(
          ([k, v]) => v != null && v !== "" && v !== false && !skip.has(k),
        )
        .map(([k, v]) => {
          const val = Array.isArray(v) ? v.join(", ") : String(v);
          return (
            `<span class="vis-prop-key">${escHtml(k)}</span>` +
            `<span class="vis-prop-val">${escHtml(val)}</span>`
          );
        })
        .join("");

      // ── Relationships ─────────────────────────────────────────────────
      // edges where this node is source (→) or target (←)
      const outEdges = edges.filter((e) => e.from === clickedId);
      const inEdges = edges.filter((e) => e.to === clickedId);

      function _relName(qname) {
        const peer = _nodeById[qname];
        return peer
          ? _nodePrimaryName(peer)
          : (qname || "").split(":").pop() || qname;
      }
      function _relDot(qname) {
        const peer = _nodeById[qname];
        const c = peer
          ? (LABEL_COLOR[peer.label] || DEFAULT_COLOR).background
          : "#64748B";
        return `<span class="vis-rel-dot" style="background:${c}"></span>`;
      }

      let relHtml = "";
      if (outEdges.length || inEdges.length) {
        relHtml = `<div class="vis-node-detail-section">Relationships</div>`;
        for (const e of outEdges) {
          relHtml += `<div class="vis-rel-row out">
            <span class="vis-rel-arrow">→</span>
            <span class="vis-rel-type">${escHtml(e.rel)}</span>
            ${_relDot(e.to)}<span class="vis-rel-peer">${escHtml(_relName(e.to))}</span>
            ${e.to_label ? `<span class="vis-rel-label">${escHtml(e.to_label)}</span>` : ""}
          </div>`;
        }
        for (const e of inEdges) {
          relHtml += `<div class="vis-rel-row in">
            <span class="vis-rel-arrow">←</span>
            <span class="vis-rel-type">${escHtml(e.rel)}</span>
            ${_relDot(e.from)}<span class="vis-rel-peer">${escHtml(_relName(e.from))}</span>
            ${e.from_label ? `<span class="vis-rel-label">${escHtml(e.from_label)}</span>` : ""}
          </div>`;
        }
      }

      detail.innerHTML = `<div class="vis-node-detail-header">
           <span class="vis-node-detail-dot" style="background:${bg}"></span>
           <span class="vis-node-detail-lbl">${escHtml(lbl)}</span>
           <span class="vis-node-detail-name">${escHtml(displayName)}</span>
         </div>
         <div class="vis-node-detail-props">${propRows || "<em style='color:#64748B'>no properties</em>"}</div>
         ${relHtml}`;
      detail.style.display = "block";
    });

    // Legend
    const legend = document.getElementById("visGraphLegend");
    if (legend) {
      const labels = [...new Set(nodes.map((n) => n.label).filter(Boolean))];
      legend.innerHTML = labels
        .map((lbl) => {
          const c = (LABEL_COLOR[lbl] || DEFAULT_COLOR).background;
          return `<span class="vis-legend-item"><i style="background:${c}"></i>${escHtml(lbl)}</span>`;
        })
        .join("");
    }
  }

  // ── Message helpers ───────────────────────────────────────────────────────
  function appendMessage(role, text) {
    const msg = document.createElement("div");
    msg.className = `message ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "U" : "AI";

    const body = document.createElement("div");
    body.className = "message-body";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    if (role === "assistant" && text) {
      bubble.innerHTML = renderMarkdown(text);
    } else {
      bubble.textContent = text;
    }

    body.appendChild(bubble);
    msg.appendChild(avatar);
    msg.appendChild(body);
    messagesEl.appendChild(msg);
    scrollBottom();
    return { msgEl: msg, bubbleEl: bubble, bodyEl: body };
  }

  // ── Utilities ─────────────────────────────────────────────────────────────
  function setBusy(val) {
    busy = val;
    textarea.disabled = val;
    sendBtn.disabled = val;
  }
  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
