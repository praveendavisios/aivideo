(function () {
  const { $, esc, icon, fmt, badge, TYPE_LABEL, KIND } = ui;

  function barChart(el, data) {
    const W = 640, H = 200, pad = { l: 28, r: 6, t: 10, b: 24 };
    const max = Math.max(4, ...data.map((d) => d.count));
    const step = Math.ceil(max / 4);
    const top = step * 4;
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const bw = iw / data.length;
    const barW = Math.max(4, bw - 6);
    const y = (v) => pad.t + ih - (v / top) * ih;
    let grid = '', bars = '', labels = '';
    for (let i = 0; i <= 4; i++) {
      const v = step * i;
      grid += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      labels += `<text x="${pad.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
    }
    data.forEach((d, i) => {
      const x = pad.l + i * bw + (bw - barW) / 2;
      const h = Math.max(0, ih - (y(d.count) - pad.t));
      const r = Math.min(4, barW / 2, h);
      const day = new Date(d.date + 'T00:00:00');
      const lbl = day.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      // bar with rounded data-end (top), square baseline
      const path = h > 0 ? `M${x},${pad.t + ih} V${y(d.count) + r} Q${x},${y(d.count)} ${x + r},${y(d.count)} H${x + barW - r} Q${x + barW},${y(d.count)} ${x + barW},${y(d.count) + r} V${pad.t + ih} Z` : '';
      bars += `<g data-tip="${esc(lbl)}: ${d.count} video${d.count === 1 ? '' : 's'}" data-x="${x + barW / 2}" data-y="${y(d.count)}">
        <rect class="bar-hit" x="${pad.l + i * bw}" y="${pad.t}" width="${bw}" height="${ih}"/>${path ? `<path class="bar" d="${path}"/>` : ''}</g>`;
      if (i % 2 === 0 || data.length <= 8) labels += `<text x="${x + barW / 2}" y="${H - 6}" text-anchor="middle">${esc(lbl)}</text>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Videos created per day, last 14 days">
      <g class="grid">${grid}</g><g class="axis">${labels}</g>${bars}</svg><div class="tooltip" hidden></div>`;
    const tip = $('.tooltip', el);
    const svg = $('svg', el);
    el.querySelectorAll('g[data-tip]').forEach((g) => {
      g.addEventListener('mouseenter', () => {
        const r = svg.getBoundingClientRect();
        tip.textContent = g.dataset.tip; tip.hidden = false;
        tip.style.left = `${(g.dataset.x / W) * r.width}px`; tip.style.top = `${(g.dataset.y / H) * r.height}px`;
      });
      g.addEventListener('mouseleave', () => { tip.hidden = true; });
    });
    $('#chart-table').innerHTML = `<table class="table"><thead><tr><th>Date</th><th>Videos</th></tr></thead><tbody>${data.map((d) => `<tr><td>${d.date}</td><td>${d.count}</td></tr>`).join('')}</tbody></table>`;
  }

  function hbars(el, entries, labels) {
    const max = Math.max(1, ...entries.map(([, v]) => v));
    el.innerHTML = entries.map(([k, v]) => `<div class="sb"><span>${esc(labels[k] || k)}</span>
      <div class="track" role="presentation"><div class="fill" style="width:${(v / max) * 100}%"></div></div><span class="num">${v}</span></div>`).join('');
  }

  const videoRow = (v) => `<li>
      ${v.has_thumbnail ? `<img class="thumb-sm" src="/media/thumb/${v.id}" alt="">` : `<span class="thumb-sm">${icon('library')}</span>`}
      <div class="main-col"><a class="t" href="/videos/${v.id}">${esc(v.title || v.prompt || 'Untitled')}</a>
      <div class="subtle">${TYPE_LABEL[v.video_type]} · ${fmt.ago(v.completed_at || v.created_at)}${v.duration ? ' · ' + fmt.dur(v.duration) : ''}</div></div>
      ${v.has_file ? `<button class="btn ghost sm icon-only" aria-label="Play" onclick="ui.playVideo(${v.id}, ${esc(JSON.stringify(v.title))})">${icon('play', 'sm')}</button>` : badge(v.status)}
    </li>`;

  async function load() {
    try {
      const d = await api.get('/api/stats');
      Object.entries(d.stats).forEach(([k, v]) => { const el = document.querySelector(`[data-stat="${k}"]`); if (el) el.textContent = v.toLocaleString(); });
      barChart($('#chart'), d.daily);
      $('#chart-total').textContent = `${d.daily.reduce((a, b) => a + b.count, 0)} in 14 days`;
      hbars($('#statusbars'), Object.entries(d.job_status), { pending: 'Pending', queued: 'Queued', processing: 'Processing', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' });
      hbars($('#typebars'), Object.entries(d.types), TYPE_LABEL);
      $('#activity').innerHTML = d.recent_jobs.length ? d.recent_jobs.map((j) => `<li>
          <div class="main-col"><div class="t">${esc(j.video_title || j.summary || 'Job #' + j.id)}</div>
          <div class="subtle">${KIND[j.kind] || j.kind} · ${fmt.ago(j.created_at)}${j.status === 'processing' ? ' · ' + esc(j.stage) : ''}${j.error ? ' · ' + esc(j.error.slice(0, 80)) : ''}</div></div>
          ${j.status === 'processing' ? `<div class="progress" style="width:90px"><i style="width:${j.progress}%"></i></div>` : ''}
          ${badge(j.status)}${j.video_id ? `<a class="btn ghost sm icon-only" href="/videos/${j.video_id}" aria-label="Open">${icon('external', 'sm')}</a>` : ''}</li>`).join('')
        : `<li>${ui.empty('sparkles', 'No generation activity yet', 'Start with one of the quick actions above.')}</li>`;
      $('#completed').innerHTML = d.recently_completed.length ? d.recently_completed.map(videoRow).join('') : `<li style="display:block">${ui.empty('check-circle', 'Nothing completed yet', 'Finished videos appear here.')}</li>`;
      $('#uploads').innerHTML = d.latest_uploads.length ? d.latest_uploads.map(videoRow).join('') : `<li style="display:block">${ui.empty('upload', 'No uploads yet', 'Upload a video to manage and publish it.', '<a class="btn primary sm" href="/upload">Upload video</a>')}</li>`;
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  async function caps(refresh) {
    try {
      const c = await api.get('/api/capabilities' + (refresh ? '?refresh=1' : ''));
      $('#caps').innerHTML = Object.values(c).map((x) => `<div class="cap ${x.ok ? 'ok' : 'no'}">${icon(x.ok ? 'check-circle' : 'alert')}<b>${esc(x.label)}</b><span title="${esc(x.detail)}">${esc(x.detail)}</span></div>`).join('');
    } catch (e) { $('#caps').innerHTML = `<p class="subtle">${esc(e.message)}</p>`; }
  }
  $('#caps-refresh').addEventListener('click', () => caps(true));

  load(); caps(false);
  let last = -1;
  document.addEventListener('mabix:active', (e) => { if (e.detail.total || last) load(); last = e.detail.total; });
})();
