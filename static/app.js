/* ─── Theme Toggle ──────────────────────────────────────────────────────────── */
const html = document.documentElement;
const themeToggle = document.getElementById('themeToggle');
const themeIcon = themeToggle?.querySelector('.theme-icon');

function applyTheme(theme) {
    html.setAttribute('data-theme', theme);
    if (themeIcon) themeIcon.textContent = theme === 'dark' ? '🌙' : '☀️';
}

(function initTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    applyTheme(saved);
})();

themeToggle?.addEventListener('click', () => {
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('theme', next);
});

/* ─── Advanced Panel Toggle ─────────────────────────────────────────────────── */
const advToggle = document.getElementById('advToggle');
const advPanel = document.getElementById('advPanel');

advToggle?.addEventListener('click', () => {
    const isOpen = advPanel.style.display === 'grid';
    advPanel.style.display = isOpen ? 'none' : 'grid';
    advToggle.setAttribute('aria-expanded', String(!isOpen));
    advToggle.style.color = !isOpen ? 'var(--accent)' : '';
    advToggle.style.borderColor = !isOpen ? 'var(--accent)' : '';
});

/* ─── Button-group radio sync (visual active class) ────────────────────────── */
document.querySelectorAll('.btn-opt input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => {
        const group = radio.closest('.btn-group');
        group?.querySelectorAll('.btn-opt').forEach(opt => opt.classList.remove('active'));
        radio.closest('.btn-opt')?.classList.add('active');
    });
});

/* ─── Reset advanced options ────────────────────────────────────────────────── */
document.getElementById('resetBtn')?.addEventListener('click', () => {
    // Reset selects
    document.querySelectorAll('.adv-panel select').forEach(s => s.selectedIndex = 0);
    // Reset text inputs
    document.querySelectorAll('.adv-panel input[type="text"]').forEach(i => i.value = '');
    // Reset dates
    document.querySelectorAll('.adv-panel input[type="date"]').forEach(i => i.value = '');
    // Reset checkboxes
    document.querySelectorAll('.adv-panel input[type="checkbox"]').forEach(cb => {
        // Keep hl checked by default
        cb.checked = cb.id === 'hlToggle';
    });
    // Reset range sliders
    const fuzzy = document.getElementById('fuzzyRange');
    if (fuzzy) { fuzzy.value = '0'; document.getElementById('fuzzyVal').textContent = '0'; }
    const proximity = document.getElementById('proximityRange');
    if (proximity) { proximity.value = '0'; document.getElementById('proximityVal').textContent = '0'; updateProximityHint(0); }
    // Reset mm hint
    const mmHint = document.getElementById('mmHint');
    if (mmHint) mmHint.style.display = 'none';
    // Reset group by select
    const groupBy = document.getElementById('groupBySelect');
    if (groupBy) groupBy.value = '';
    // Reset operator radios to OR
    document.querySelectorAll('.btn-opt input[type="radio"]').forEach(r => {
        r.checked = r.value === 'OR';
        const btnOpt = r.closest('.btn-opt');
        if (btnOpt) btnOpt.classList.toggle('active', r.value === 'OR');
    });
    // Reset parser
    const parser = document.getElementById('parserSelect');
    if (parser) parser.value = 'edismax';
});

/* ─── Phrase mode hint ──────────────────────────────────────────────────────── */
const phraseToggle = document.getElementById('phraseToggle');
const queryInput = document.getElementById('queryInput');
phraseToggle?.addEventListener('change', () => {
    queryInput?.setAttribute('placeholder',
        phraseToggle.checked
            ? 'Enter an exact phrase to search…'
            : 'Enter keywords, code snippets, phrases…'
    );
});

/* ─── Keyboard shortcut: ⌘K / Ctrl+K focuses search ────────────────────────── */
document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        queryInput?.focus();
    }
});

/* ─── Active facet indicator on "refine by" links ───────────────────────────── */
document.querySelectorAll('.facet-link').forEach(link => {
    link.addEventListener('click', e => {
        // Brief visual feedback
        link.style.opacity = '0.6';
    });
});

/* ─── Number formatter for Jinja (injected via data attribute fallback) ─────── */
// Jinja's format_number isn't built-in; we apply it client-side too
document.querySelectorAll('.num-found').forEach(el => {
    const raw = el.textContent.trim().replace(/\D/g, '');
    if (raw) {
        const n = parseInt(raw, 10);
        el.textContent = n.toLocaleString() + ' results';
    }
});

/* ─── Proximity Search — live query hint ─────────────────────────────────── */
function updateProximityHint(val) {
    const hint = document.getElementById('proximityHint');
    const qVal = document.getElementById('queryInput')?.value || '…';
    if (!hint) return;
    if (parseInt(val, 10) > 0) {
        hint.style.display = 'block';
        hint.innerHTML = `Query: <code>"${qVal}"~${val}</code>`;
    } else {
        hint.style.display = 'none';
    }
}

/* ─── Fuzzy range live label (also handled by inline oninput) ───────────────── */
document.getElementById('fuzzyRange')?.addEventListener('input', e => {
    document.getElementById('fuzzyVal').textContent = e.target.value;
});

/* ─── Search form: show loading state ──────────────────────────────────────── */
document.getElementById('searchForm')?.addEventListener('submit', () => {
    const btn = document.getElementById('searchBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="animation:spin 1s linear infinite;width:16px;height:16px">
        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-.18-3.22"/>
      </svg>
      Searching…`;
    }
});

/* Spin animation for the loading icon */
const spinStyle = document.createElement('style');
spinStyle.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
document.head.appendChild(spinStyle);
