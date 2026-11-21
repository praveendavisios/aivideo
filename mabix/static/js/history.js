(function () {
  const { $, $$, esc, icon, fmt, badge, TYPE_LABEL, KIND } = ui;
  const state = { status: '', kind: '', q: '', page: 1 };
  let timer;

  async function load() {
    const qs = new URLSearchParams({ status: state.status, kind: state.kind, q: state.q, page: state.page, per_page: 20 });
    try {
      const d = await api.get('/api/jobs?' + qs);
      $('#rows').innerHTML = d.items.length ? d.items.map((j) => `<tr>
          <td class="mono">#${j.id}${j.retry_of ? `<div class="subtle">retry of #${j.retry_of}</div>` : ''}</td>
          <td style="white-space:nowrap"><b>${KIND[j.kind] || j.kind}</b><div class="subtle">${TYPE_LABEL[j.video_type] || ''}</div></td>
          <td><div class="truncate" title="${esc(j.summary)}">${j.video_id ? `<a href="/videos/${j.video_id}">${esc(j.video_title || j.summary)}</a>` : esc(j.summary)}</div>
            ${j.error ? `<div class="subtle truncate" style="color:var(--bad)" title="${esc(j.error)}">${esc(j.error)}</div>` : j.status === 'processing' ? `<div class="subtle truncate">${esc(j.stage)}</div>` : ''}</td>
          <td>${badge(j.status)}</td>
          <td>${['processing', 'completed'].includes(j.status) ? `<div class="row" style="flex-wrap:nowrap"><div class="progress" style="width:90px"><i style="width:${j.status === 'completed' ? 100 : j.progress}%"></i></div><span class="subtle">${j.status === 'completed' ? 100 : j.progress}%</span></div>` : '<span class="subtle">—</span>'}</td>
          <td class="subtle" style="white-space:nowrap" title="${esc(j.created_at)}">${fmt.date(j.created_at)}</td>
          <td class="subtle" style="white-space:nowrap">${j.started_at ? fmt.elapsed(j.started_at, j.finished_at) : '—'}${j.finished_at ? `<div>done ${fmt.ago(j.finished_at)}</div>` : ''}</td>
          <td style="white-space:nowrap">
            <button class="btn ghost sm icon-only" data-log="${j.id}" title="View log" aria-label="View log">${icon('eye', 'sm')}</button>
            ${['failed', 'cancelled'].includes(j.status) && j.video_id ? `<button class="btn ghost sm icon-only" data-retry="${j.id}" title="Retry" aria-label="Retry">${icon('refresh', 'sm')}</button>` : ''}
            ${['pending', 'queued', 'processing'].includes(j.status) && !j.cancel_requested ? `<button class="btn ghost sm icon-only danger" data-cancel="${j.id}" title="Cancel" aria-label="Cancel">${icon('stop', 'sm')}</button>` : ''}
          </td></tr>`).join('')
        : `<tr><td colspan="8">${ui.empty('clock', state.status || state.kind || state.q ? 'No matching jobs' : 'No generation history yet', 'Generation requests appear here with their status, timing and errors.')}</td></tr>`;
      $('#pager').innerHTML = d.pages > 1 ? `<button class="btn sm" data-p="${d.page - 1}" ${d.page <= 1 ? 'disabled' : ''}>Previous</button>
        <span class="subtle">Page ${d.page} of ${d.pages} · ${d.total} jobs</span><button class="btn sm" data-p="${d.page + 1}" ${d.page >= d.pages ? 'disabled' : ''}>Next</button>` : '';
    } catch (e) { ui.toast(e.message, 'bad'); }
    clearTimeout(timer);
    if ($('#auto').checked) timer = setTimeout(load, 3000);
  }

  async function showLog(id) {
    try {
      const { job } = await api.get(`/api/jobs/${id}`);
      const m = ui.modal({ title: `${KIND[job.kind] || job.kind} job #${job.id}`, wide: true,
        body: `<div class="row" style="margin-bottom:12px">${badge(job.status)}<span class="subtle">Requested ${fmt.date(job.created_at)} · Ran ${fmt.elapsed(job.started_at, job.finished_at)}</span></div>
          <p style="margin-bottom:12px"><b>Request:</b> ${esc(job.summary)}</p>
          ${job.error ? `<div class="notice bad" style="margin-bottom:12px">${icon('alert')}<div class="mono" style="white-space:pre-wrap">${esc(job.error)}</div></div>` : ''}
          <pre class="log">${esc(job.log)}</pre>`,
        footer: job.video_id ? `<a class="btn primary" href="/videos/${job.video_id}">Open video</a>` : '' });
      const pre = $('pre', m.el); pre.scrollTop = pre.scrollHeight;
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  $('#rows').addEventListener('click', async (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.log) return showLog(b.dataset.log);
    if (b.dataset.retry) { try { await api.post(`/api/jobs/${b.dataset.retry}/retry`); ui.toast('Job re-queued', 'info'); load(); } catch (er) { ui.toast(er.message, 'bad'); } }
    if (b.dataset.cancel && await ui.confirmDialog({ title: 'Cancel job?', message: 'Queued jobs stop immediately; running jobs stop at the next safe point.', confirmText: 'Cancel job', danger: true })) {
      try { await api.post(`/api/jobs/${b.dataset.cancel}/cancel`); ui.toast('Cancellation requested', 'info'); load(); } catch (er) { ui.toast(er.message, 'bad'); }
    }
  });
  $('#filters').addEventListener('click', (e) => { const p = e.target.closest('[data-status]'); if (!p) return; state.status = p.dataset.status; state.page = 1; $$('#filters .pill').forEach((x) => x.classList.toggle('on', x === p)); load(); });
  $('#kind').addEventListener('change', () => { state.kind = $('#kind').value; state.page = 1; load(); });
  let deb; $('#q').addEventListener('input', () => { clearTimeout(deb); deb = setTimeout(() => { state.q = $('#q').value.trim(); state.page = 1; load(); }, 300); });
  $('#pager').addEventListener('click', (e) => { const b = e.target.closest('[data-p]'); if (b) { state.page = +b.dataset.p; load(); } });
  $('#auto').addEventListener('change', load);
  load();
})();
