(function () {
  const { $, $$, icon } = ui;
  const params = new URLSearchParams(location.search);
  const state = { filter: params.get('filter') || 'all', q: params.get('q') || '', sort: params.get('sort') || 'newest', type: params.get('type') || '', page: +params.get('page') || 1 };
  let timer;

  function syncControls() {
    $$('#filters .pill').forEach((p) => { const on = p.dataset.filter === state.filter; p.classList.toggle('on', on); p.setAttribute('aria-selected', on); });
    $('#q').value = state.q; $('#sort').value = state.sort; $('#type').value = state.type;
  }

  async function load() {
    const qs = new URLSearchParams({ filter: state.filter, q: state.q, sort: state.sort, page: state.page, per_page: 12 });
    if (state.type) qs.set('type', state.type);
    history.replaceState(null, '', '?' + qs.toString().replace(/&?per_page=12/, ''));
    if (!$('#grid').children.length) $('#grid').innerHTML = Array(8).fill('<div class="card skeleton" style="height:280px"></div>').join('');
    try {
      const d = await api.get('/api/videos?' + qs);
      $('#total').textContent = `${d.total} video${d.total === 1 ? '' : 's'}`;
      if (!d.items.length) {
        const searching = state.q || state.filter !== 'all' || state.type;
        $('#grid').innerHTML = `<div class="card" style="grid-column:1/-1">${searching
          ? ui.empty('search', 'No videos match', 'Try a different search or filter.', '<button class="btn" id="clear">Clear filters</button>')
          : ui.empty('library', 'Your library is empty', 'Create an AI video or upload one to get started.', '<a class="btn primary" href="/studio/short">Create AI video</a> <a class="btn" href="/upload">Upload</a>')}</div>`;
        $('#pager').innerHTML = '';
        $('#clear')?.addEventListener('click', () => { Object.assign(state, { filter: 'all', q: '', type: '', page: 1 }); syncControls(); load(); });
        return;
      }
      $('#grid').innerHTML = d.items.map((v) => ui.videoCard(v)).join('');
      $('#pager').innerHTML = d.pages > 1 ? `<button class="btn sm" data-p="${d.page - 1}" ${d.page <= 1 ? 'disabled' : ''}>Previous</button>
        <span class="subtle">Page ${d.page} of ${d.pages}</span><button class="btn sm" data-p="${d.page + 1}" ${d.page >= d.pages ? 'disabled' : ''}>Next</button>` : '';
      clearTimeout(timer);
      if (d.items.some((v) => ['scripting', 'queued', 'processing', 'storyboarding'].includes(v.status))) timer = setTimeout(load, 4000);
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  $('#filters').addEventListener('click', (e) => { const p = e.target.closest('[data-filter]'); if (!p) return; state.filter = p.dataset.filter; state.page = 1; syncControls(); load(); });
  let deb;
  $('#q').addEventListener('input', () => { clearTimeout(deb); deb = setTimeout(() => { state.q = $('#q').value.trim(); state.page = 1; load(); }, 300); });
  $('#sort').addEventListener('change', () => { state.sort = $('#sort').value; load(); });
  $('#type').addEventListener('change', () => { state.type = $('#type').value; state.page = 1; load(); });
  $('#pager').addEventListener('click', (e) => { const b = e.target.closest('[data-p]'); if (b) { state.page = +b.dataset.p; load(); scrollTo({ top: 0, behavior: 'smooth' }); } });
  ui.bindVideoActions($('#grid'), load);
  syncControls(); load();
})();
