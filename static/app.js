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

    // Clear the main search input and go back to home
    if (queryInput) queryInput.value = '';
    window.location.href = window.location.pathname.replace(/\/search$/, '/') || '/';
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

/* ─── Sticky search bar on scroll ──────────────────────────────────────────── */
(function () {
    const bar = document.getElementById('stickySearchBar');
    const stickyInput = document.getElementById('stickyQueryInput');
    const stickyBtn = document.getElementById('stickySearchBtn');
    const hero = document.querySelector('.hero');
    const facetsPanel = document.querySelector('.facets-panel');
    if (!bar || !hero) return;

    const STICKY_H = 56; // px — matches the bar's rendered height

    function updateBar() {
        const heroBottom = hero.getBoundingClientRect().bottom;
        const show = heroBottom <= 0;
        bar.classList.toggle('is-visible', show);
        bar.setAttribute('aria-hidden', String(!show));
        if (facetsPanel) {
            facetsPanel.style.top = show ? STICKY_H + 8 + 'px' : '0px';
        }
    }

    window.addEventListener('scroll', updateBar, { passive: true });
    updateBar(); // run on load in case page refreshed mid-scroll

    // Submit sticky bar → replace q in current URL and navigate
    function submitSticky() {
        const q = stickyInput?.value?.trim();
        if (!q) { stickyInput?.focus(); return; }
        const url = new URL(window.location.href);
        url.searchParams.set('q', q);
        url.searchParams.delete('page'); // reset to page 1
        window.location.href = url.toString();
    }

    stickyBtn?.addEventListener('click', submitSticky);
    stickyInput?.addEventListener('keydown', e => {
        if (e.key === 'Enter') submitSticky();
    });
})();

/* ─── Fuzzy range live label (also handled by inline oninput) ───────────────── */
document.getElementById('fuzzyRange')?.addEventListener('input', e => {
    document.getElementById('fuzzyVal').textContent = e.target.value;
});

/* ─── Search form: show loading state ──────────────────────────────────────── */
document.getElementById('searchForm')?.addEventListener('submit', (e) => {
    const q = queryInput?.value?.trim();
    if (!q) {
        e.preventDefault();
        queryInput?.focus();
        return;
    }
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

/* ─── Copy file path to clipboard ─────────────────────────────────────────── */
function copyPath(filePath) {
    navigator.clipboard.writeText(filePath).then(() => {
        // Brief visual feedback on the button that was clicked
        const btn = document.activeElement;
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = '✓ Copied!';
            setTimeout(() => { btn.textContent = orig; }, 1500);
        }
    }).catch(() => {
        // Fallback for older browsers
        const ta = document.createElement('textarea');
        ta.value = filePath;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
    });
}

/* ─── Open local file via server API (bypasses browser file:// block) ────────── */
function openLocalFile(filePath) {
    const url = '/ocr-search/api/open-file?path=' + encodeURIComponent(filePath);
    fetch(url)
        .then(r => r.json())
        .then(data => {
            if (!data.ok) {
                alert('Cannot open file: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(() => alert('Failed to contact server.'));
}

/* ─── Spin animation for the loading icon */
const spinStyle = document.createElement('style');
spinStyle.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
document.head.appendChild(spinStyle);
