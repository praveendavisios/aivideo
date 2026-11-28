/* Shared UI helpers: toasts, dialogs, formatting, video cards, theme, nav. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, cls = '') => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;

  function toast(msg, type = 'good', ms = 4200) {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `${icon(type === 'bad' ? 'alert' : type === 'info' ? 'info' : 'check-circle')}<div>${esc(msg)}</div>`;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 320); }, ms);
  }

  function modal({ title, body, wide = false, footer = '' }) {
    const bg = document.createElement('div');
    bg.className = 'modal-bg';
    bg.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="mh"><h2>${esc(title)}</h2><button class="btn ghost sm icon-only" data-x aria-label="Close">${icon('x', 'sm')}</button></div>
      <div class="mb"></div>${footer ? `<div class="mf">${footer}</div>` : ''}</div>`;
    const mb = $('.mb', bg);
    if (typeof body === 'string') mb.innerHTML = body; else if (body) mb.appendChild(body);
    const prev = document.activeElement;
    const close = () => { bg.remove(); document.removeEventListener('keydown', onKey); prev && prev.focus && prev.focus(); bg.dispatchEvent(new Event('closed')); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    bg.addEventListener('click', (e) => { if (e.target === bg || e.target.closest('[data-x]')) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(bg);
    const focusable = $('input, textarea, select, .mf .btn.primary, .mf .btn', bg);
    (focusable || $('[data-x]', bg)).focus();
    return { el: bg, close };
  }

  function confirmDialog({ title = 'Are you sure?', message = '', confirmText = 'Confirm', danger = false }) {
    return new Promise((resolve) => {
      let answered = false;
      const m = modal({
        title, body: `<p class="muted">${esc(message)}</p>`,
        footer: `<button class="btn" data-no>Cancel</button><button class="btn ${danger ? 'danger solid' : 'primary'}" data-yes>${esc(confirmText)}</button>`,
      });
      $('[data-yes]', m.el).focus();
      m.el.addEventListener('click', (e) => {
        if (e.target.closest('[data-yes]')) { answered = true; m.close(); resolve(true); }
        if (e.target.closest('[data-no]')) { answered = true; m.close(); resolve(false); }
      });
      m.el.addEventListener('closed', () => { if (!answered) resolve(false); });
    });
  }

  function playVideo(id, title) {
    modal({ title: title || 'Preview', wide: true,
      body: `<div class="player"><video src="/media/video/${id}" controls autoplay playsinline></video></div>`,
      footer: `<a class="btn" href="/media/video/${id}?download=1">${icon('download', 'sm')} Download</a><a class="btn primary" href="/videos/${id}">Open details</a>` });
  }

  const fmt = {
    bytes(n) { if (!n && n !== 0) return '—'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`; },
    dur(s) { if (!s && s !== 0) return ''; s = Math.round(s); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, '0')}`; },
    date(iso) { if (!iso) return '—'; const d = new Date(iso); return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); },
    ago(iso) {
      if (!iso) return '—';
      const s = (Date.now() - new Date(iso).getTime()) / 1000;
      if (s < 45) return 'just now'; if (s < 3600) return `${Math.round(s / 60)} min ago`;
      if (s < 86400) return `${Math.round(s / 3600)} h ago`; if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
      return fmt.date(iso);
    },
    elapsed(a, b) { if (!a) return '—'; const s = Math.max(0, ((b ? new Date(b) : new Date()) - new Date(a)) / 1000); return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`; },
    res(v) { return v.width && v.height ? `${v.width}×${v.height}` : ''; },
  };

  const TYPE_LABEL = { short: 'AI Short', animation: 'Animation', story: 'Story', answer: 'Short Answer', news: 'News', upload: 'Uploaded' };
  const STATUS = {
    completed: ['good', 'Completed'], failed: ['bad', 'Failed'], cancelled: ['', 'Cancelled'], processing: ['info live', 'Processing'],
    queued: ['warn', 'Queued'], pending: ['', 'Pending'], scripting: ['info live', 'Writing script'], storyboarding: ['info live', 'Storyboarding'],
    review: ['brand', 'Ready for review'], draft: ['', 'Draft'],
    published: ['good', 'Published'], publishing: ['info live', 'Publishing'], unpublished: ['', 'Not published'],
  };
  const badge = (s) => { const [c, l] = STATUS[s] || ['', s]; return `<span class="badge ${c}"><span class="dot"></span>${esc(l)}</span>`; };
  const KIND = { script: 'Script', storyboard: 'Storyboard', render: 'Render', news: 'News video', publish: 'Publish' };

  function videoCard(v, opts = {}) {
    const thumb = v.has_thumbnail ? `<img src="/media/thumb/${v.id}" alt="" loading="lazy">`
      : `<span class="ph">${icon(v.status === 'failed' ? 'alert' : v.status === 'review' ? 'edit' : ['scripting', 'queued', 'processing', 'storyboarding'].includes(v.status) ? 'loader' : 'library', 'lg' + (['scripting', 'queued', 'processing', 'storyboarding'].includes(v.status) ? ' spin' : ''))}<span>${esc((STATUS[v.status] || [, v.status])[1])}</span></span>`;
    const canPlay = v.has_file && v.status === 'completed';
    const pubBadge = v.publish_status === 'published' ? `<span class="badge good">Published</span>` : '';
    return `<article class="card vcard" data-id="${v.id}">
      <button class="vthumb" ${canPlay ? `data-play="${v.id}" aria-label="Play ${esc(v.title)}"` : `data-open="${v.id}" aria-label="Open ${esc(v.title)}"`}>
        ${thumb}<span class="tl"><span class="type-pill ${v.source_type === 'upload' ? 'upload' : ''}">${TYPE_LABEL[v.video_type] || v.video_type}</span></span>
        ${v.duration ? `<span class="dur">${fmt.dur(v.duration)}</span>` : ''}${canPlay ? `<span class="play">${icon('play')}</span>` : ''}
      </button>
      <div class="vbody">
        <a class="t" href="/videos/${v.id}">${esc(v.title || v.prompt || 'Untitled')}</a>
        <div class="vmeta"><span>${fmt.ago(v.created_at)}</span>${v.width ? `<span>${fmt.res(v)}</span>` : ''}</div>
        <div class="row" style="gap:6px;margin-top:4px">${badge(v.status)}${pubBadge}</div>
      </div>
      ${opts.actions === false ? '' : `<div class="vactions">
        ${canPlay ? `<button class="btn ghost sm icon-only" data-play="${v.id}" title="Play" aria-label="Play">${icon('play', 'sm')}</button>
        <a class="btn ghost sm icon-only" href="/media/video/${v.id}?download=1" title="Download" aria-label="Download">${icon('download', 'sm')}</a>` : ''}
        <a class="btn ghost sm icon-only" href="/videos/${v.id}" title="Details & edit" aria-label="Details">${icon('edit', 'sm')}</a>
        <span class="spacer"></span>
        <button class="btn ghost sm icon-only danger" data-delete="${v.id}" title="Delete" aria-label="Delete">${icon('trash', 'sm')}</button>
      </div>`}
    </article>`;
  }

  async function deleteVideo(id, title) {
    const ok = await confirmDialog({ title: 'Delete video?', message: `“${title || 'This video'}” and its files will be permanently deleted.`, confirmText: 'Delete', danger: true });
    if (!ok) return false;
    try { await api.del(`/api/videos/${id}`); toast('Video deleted'); return true; }
    catch (e) { toast(e.message, 'bad'); return false; }
  }

  /* Wire play/open/delete buttons inside a container; onChange runs after delete. */
  function bindVideoActions(root, onChange) {
    root.addEventListener('click', async (e) => {
      const p = e.target.closest('[data-play]');
      if (p) { const card = p.closest('[data-id]'); playVideo(p.dataset.play, card && $('.t', card)?.textContent); return; }
      const o = e.target.closest('[data-open]');
      if (o) { location.href = `/videos/${o.dataset.open}`; return; }
      const d = e.target.closest('[data-delete]');
      if (d) { const card = d.closest('[data-id]'); if (await deleteVideo(d.dataset.delete, card && $('.t', card)?.textContent)) onChange && onChange(); }
    });
  }

  function empty(iconName, title, text, action = '') {
    return `<div class="empty"><div class="ei">${icon(iconName, 'lg')}</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;
  }

  function busy(btn, on, label) {
    if (on) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `${icon('loader', 'sm spin')} ${esc(label || 'Working…')}`; }
    else { btn.disabled = false; if (btn.dataset.label) btn.innerHTML = btn.dataset.label; }
  }

  /* theme */
  function currentTheme() {
    const t = document.documentElement.getAttribute('data-theme');
    if (t) return t;
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function syncThemeIcon() { const u = $('#theme-toggle use'); if (u) u.setAttribute('href', currentTheme() === 'dark' ? '#i-sun' : '#i-moon'); }
  $('#theme-toggle')?.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('mabix-theme', next); } catch (e) { /* storage blocked */ }
    syncThemeIcon();
  });
  syncThemeIcon();

  /* nav */
  $('[data-open-nav]')?.addEventListener('click', () => document.body.classList.add('nav-open'));
  $('[data-close-nav]')?.addEventListener('click', () => document.body.classList.remove('nav-open'));
  $$('.nav-group').forEach((g) => {
    const key = 'mabix-nav-' + g.dataset.group;
    const sub = document.getElementById(g.getAttribute('aria-controls'));
    let open = true;
    try { open = localStorage.getItem(key) !== '0'; } catch (e) { /* default open */ }
    if (sub.querySelector('.active')) open = true;
    g.setAttribute('aria-expanded', String(open)); sub.hidden = !open;
    g.addEventListener('click', () => {
      const o = g.getAttribute('aria-expanded') !== 'true';
      g.setAttribute('aria-expanded', String(o)); sub.hidden = !o;
      try { localStorage.setItem(key, o ? '1' : '0'); } catch (e) { /* ignore */ }
    });
  });
  $('[data-confirm-logout]')?.addEventListener('submit', async (e) => {
    if (e.target.dataset.ok) return;
    e.preventDefault();
    if (await confirmDialog({ title: 'Sign out?', message: 'You will need to sign in again to access the studio.', confirmText: 'Sign out' })) {
      e.target.dataset.ok = '1'; e.target.submit();
    }
  });

  /* active-jobs indicator */
  async function pollActive() {
    if (!['admin', 'teacher'].includes(document.body.dataset.role)) return;  // studio jobs are staff-only
    try {
      const d = await api.get('/api/jobs?active=1&per_page=100');
      const n = d.total;
      for (const id of ['#top-active', '#nav-active']) {
        const el = $(id); if (!el) continue;
        el.hidden = !n; $('span:last-child', el).textContent = id === '#top-active' ? `${n} active job${n === 1 ? '' : 's'}` : n;
      }
      document.dispatchEvent(new CustomEvent('mabix:active', { detail: d }));
    } catch (e) { /* silent */ }
  }
  pollActive();
  setInterval(pollActive, 5000);

  window.ui = { $, $$, esc, icon, toast, modal, confirmDialog, playVideo, fmt, badge, videoCard, bindVideoActions,
    deleteVideo, empty, busy, TYPE_LABEL, STATUS, KIND };
})();
