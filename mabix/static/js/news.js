(function () {
  const { $, $$, esc, icon } = ui;
  const selected = new Set();
  let items = [];

  function updateSel() {
    $('#sel-count').textContent = `${selected.size} selected`;
    $('#generate').disabled = !selected.size;
  }

  async function loadNews() {
    const t = $('#articles');
    t.innerHTML = '<tbody><tr><td><div class="skeleton" style="height:180px"></div></td></tr></tbody>';
    try {
      const d = await api.get(`/api/news?category=${$('#category').value}&count=${$('#count').value}`);
      items = d.items;
      if (!items.length) { t.innerHTML = `<tbody><tr><td>${ui.empty('news', 'No articles found', 'Try another category.')}</td></tr></tbody>`; return; }
      t.innerHTML = `<thead><tr><th style="width:40px"><input type="checkbox" id="all" aria-label="Select all"></th><th>Headline</th><th>Published</th></tr></thead>
        <tbody>${items.map((n, i) => `<tr><td><input type="checkbox" data-i="${i}" ${selected.has(n.title) ? 'checked' : ''} aria-label="Select article"></td>
          <td><div style="font-weight:600">${esc(n.title)}</div>${n.link ? `<a class="subtle" href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.source || 'Source')} ${icon('external', 'sm')}</a>` : ''}</td>
          <td class="subtle" style="white-space:nowrap">${n.published ? ui.fmt.date(n.published) : ''}</td></tr>`).join('')}</tbody>`;
    } catch (e) {
      t.innerHTML = `<tbody><tr><td>${ui.empty('alert', 'Could not load news', e.message, '<button class="btn" onclick="location.reload()">Retry</button>')}</td></tr></tbody>`;
    }
  }

  $('#articles').addEventListener('change', (e) => {
    if (e.target.id === 'all') {
      $$('#articles tbody input[type=checkbox]').forEach((c) => { c.checked = e.target.checked; c.dispatchEvent(new Event('change', { bubbles: true })); });
      return;
    }
    const i = e.target.dataset.i;
    if (i === undefined) return;
    if (e.target.checked) selected.add(items[i].title); else selected.delete(items[i].title);
    updateSel();
  });
  $('#category').addEventListener('change', loadNews);
  $('#count').addEventListener('change', loadNews);
  $('#reload').addEventListener('click', loadNews);
  $$('input[name=mode]').forEach((r) => r.addEventListener('change', () => { $('#motion-field').hidden = $('input[name=mode]:checked').value !== 'review'; }));

  $('#generate').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    ui.busy(btn, true, 'Queuing…');
    const mode = $('input[name=mode]:checked').value;
    try {
      const r = await api.post('/api/news/generate', {
        articles: [...selected], format: $('input[name=format]:checked').value, ai_only: $('#ai_only').checked,
        mode, category: $('#category').value, config: { motion: $('#motion').value },
      });
      ui.toast(`${r.created.length} news video${r.created.length === 1 ? '' : 's'} queued`);
      selected.clear(); updateSel(); $$('#articles input[type=checkbox]').forEach((c) => { c.checked = false; });
      if (mode === 'review' && r.created.length === 1) location.href = `/videos/${r.created[0].video_id}`;
      recent();
    } catch (er) { ui.toast(er.message, 'bad'); }
    ui.busy(btn, false); updateSel();
  });

  async function recent() {
    try {
      const d = await api.get('/api/videos?filter=news&per_page=6');
      $('#recent').innerHTML = d.items.length ? d.items.map((v) => `<li>
        ${v.has_thumbnail ? `<img class="thumb-sm" src="/media/thumb/${v.id}" alt="">` : `<span class="thumb-sm">${icon('news')}</span>`}
        <div class="main-col"><a class="t" href="/videos/${v.id}">${esc(v.title || v.prompt)}</a><div class="subtle">${ui.fmt.ago(v.created_at)}</div></div>${ui.badge(v.status)}</li>`).join('')
        : `<li style="display:block">${ui.empty('news', 'No news videos yet', 'Select articles and generate.')}</li>`;
    } catch (e) { /* ignore */ }
  }
  document.addEventListener('mabix:active', recent);
  loadNews(); recent();
})();
