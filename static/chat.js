/* chat.js — RAG Chat frontend logic */
(() => {
  "use strict";

  // ── Markdown renderer setup ───────────────────────────────────────────────
  if (typeof marked !== "undefined") {
    marked.setOptions({
      gfm: true,
      breaks: true,
      highlight: (typeof hljs !== "undefined")
        ? (code, lang) => {
            const language = hljs.getLanguage(lang) ? lang : "plaintext";
            return hljs.highlight(code, { language }).value;
          }
        : null,
    });
  }

  function renderMarkdown(text) {
    if (typeof marked === "undefined") return escHtml(text);
    // marked returns HTML — safe because we control the content source (LLM)
    return marked.parse(text);
  }

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const root        = document.getElementById("chatRoot");
  const CHAT_URL    = root?.dataset.chatUrl    || "/api/chat";
  const STATUS_URL  = root?.dataset.statusUrl  || "/api/chat/status";
  const messagesEl  = document.getElementById("chatMessages");
  const welcomeEl   = document.getElementById("chatWelcome");
  const textarea    = document.getElementById("chatInput");
  const sendBtn     = document.getElementById("sendBtn");
  const modeSel     = { value: "semantic" };
  const topKInput   = document.getElementById("topKInput");
  const statusDot   = document.getElementById("statusDot");
  const statusText  = document.getElementById("lmStatusText");
  const clearBtn    = document.getElementById("clearBtn");

  // ── State ─────────────────────────────────────────────────────────────────
  let history = [];   // [{role, content}]
  let busy    = false;

  // ── LM Studio health check ────────────────────────────────────────────────
  // ── Proxy health check ────────────────────────────────────────────────────
  async function checkStatus() {
    try {
      const res = await fetch(STATUS_URL);
      if (!res.ok) throw new Error();
      const data = await res.json();
      statusDot.className    = "status-dot " + (data.available ? "online" : "offline");
      statusText.textContent = data.available ? "Proxy online" : "Proxy unreachable";
    } catch {
      statusDot.className    = "status-dot offline";
      statusText.textContent = "Proxy unreachable";
    }
  }

  checkStatus();
  setInterval(checkStatus, 15_000);

  // ── Textarea auto-resize ──────────────────────────────────────────────────
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
  });

  textarea.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy) send();
    }
  });

  sendBtn.addEventListener("click", () => { if (!busy) send(); });
  clearBtn.addEventListener("click", clearChat);

  // ── Suggestion chips ──────────────────────────────────────────────────────
  document.querySelectorAll(".suggestion-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      textarea.value = chip.textContent.trim();
      textarea.dispatchEvent(new Event("input"));
      send();
    });
  });

  // ── Clear ─────────────────────────────────────────────────────────────────
  function clearChat() {
    history = [];
    messagesEl.innerHTML = "";
    welcomeEl.style.display = "flex";
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  async function send() {
    const text = textarea.value.trim();
    if (!text) return;
    setBusy(true);

    if (welcomeEl) welcomeEl.style.display = "none";

    // Add user bubble
    appendMessage("user", text);
    history.push({ role: "user", content: text });
    textarea.value = "";
    textarea.style.height = "auto";

    // Add placeholder assistant bubble
    const { bubbleEl, bodyEl } = appendMessage("assistant", "");
    bubbleEl.classList.add("streaming");

    let fullAnswer = "";
    let contextDocs = [];

    try {
      const res = await fetch(CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: history.slice(0, -1),   // exclude current user msg (server adds it)
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
        buf = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;

          try {
            const evt = JSON.parse(raw);
            if (evt.type === "context") {
              contextDocs = evt.docs || [];
            } else if (evt.type === "token") {
              fullAnswer += evt.text;
              bubbleEl.innerHTML = renderMarkdown(fullAnswer);
              scrollBottom();
            } else if (evt.type === "error") {
              throw new Error(evt.text);
            }
          } catch (parseErr) {
            if (parseErr.message && !parseErr.message.includes("JSON")) throw parseErr;
          }
        }
      }
    } catch (err) {
      bubbleEl.innerHTML = `<span class="error-banner">Error: ${escHtml(err.message)}</span>`;
    } finally {
      bubbleEl.classList.remove("streaming");
      setBusy(false);
    }

    // Append context sources below the bubble
    if (contextDocs.length) {
      appendContextSources(bodyEl, contextDocs);
    }

    // Add assistant reply to history
    if (fullAnswer) {
      history.push({ role: "assistant", content: fullAnswer });
    }

    scrollBottom();
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

  function appendContextSources(bodyEl, docs) {
    const toggle = document.createElement("button");
    toggle.className = "context-toggle";
    toggle.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg> ${docs.length} sources`;

    const sourcesEl = document.createElement("div");
    sourcesEl.className = "context-sources";

    docs.forEach(doc => {
      const docEl = document.createElement("div");
      docEl.className = "context-doc";
      const score = doc.score != null ? (doc.score * 100).toFixed(0) + "%" : "";
      docEl.innerHTML = `
        <div class="context-doc-header">
          <span class="context-doc-file">${escHtml(doc.file || doc.rel_path || doc.file_path || "unknown")}</span>
          ${score ? `<span class="context-doc-score">${score}</span>` : ""}
        </div>
        <div class="context-doc-text">${escHtml((doc.text || "").slice(0, 300))}</div>
      `;
      sourcesEl.appendChild(docEl);
    });

    toggle.addEventListener("click", () => {
      sourcesEl.classList.toggle("open");
      const isOpen = sourcesEl.classList.contains("open");
      toggle.innerHTML = toggle.innerHTML.replace(
        isOpen ? "sources" : "sources",
        "sources"
      );
    });

    bodyEl.appendChild(toggle);
    bodyEl.appendChild(sourcesEl);
  }

  // ── Utilities ─────────────────────────────────────────────────────────────
  function setBusy(val) {
    busy = val;
    textarea.disabled  = val;
    sendBtn.disabled   = val;
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
