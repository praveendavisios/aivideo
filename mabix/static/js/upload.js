(function () {
  const { $, esc, icon, fmt } = ui;
  const queue = [];
  let running = false;

  function validate(file) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!window.EXTS.includes(ext)) return `Unsupported format .${ext}`;
    if (file.size > window.MAX_MB * 1024 * 1024) return `File is larger than ${window.MAX_MB} MB`;
    if (!file.size) return 'File is empty';
    return null;
  }

  function render() {
    if (!queue.length) return;
    const done = queue.filter((q) => q.status === 'completed').length;
    $('#queue-summary').textContent = `${done}/${queue.length} completed`;
    $('#queue').innerHTML = queue.map((q, i) => {
      const st = { waiting: ['', 'Waiting'], uploading: ['info live', 'Uploading'], processing: ['info live', 'Processing'], completed: ['good', 'Completed'], failed: ['bad', 'Failed'] }[q.status];
      return `<div class="upitem">
        <div style="min-width:0"><div class="t" style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(q.file.name)}</div>
          <div class="subtle">${fmt.bytes(q.file.size)}${q.error ? ' · <span style="color:var(--bad)">' + esc(q.error) + '</span>' : ''}</div></div>
        <div class="row"><span class="badge ${st[0]}"><span class="dot"></span>${st[1]}${q.status === 'uploading' ? ' ' + Math.round(q.progress * 100) + '%' : ''}</span>
          ${q.status === 'failed' && q.retryable ? `<button class="btn sm" data-retry="${i}">${icon('refresh', 'sm')} Retry</button>` : ''}
          ${q.status === 'uploading' ? `<button class="btn sm" data-abort="${i}">Cancel</button>` : ''}
          ${q.status === 'completed' ? `<a class="btn sm" href="/videos/${q.video.id}">Open</a>` : ''}</div>
        <div class="progress" style="grid-column:1/-1"><i style="width:${q.status === 'completed' ? 100 : Math.round(q.progress * 100)}%"></i></div>
      </div>`;
    }).join('');
  }

  async function pump() {
    if (running) return;
    running = true;
    let q;
    while ((q = queue.find((x) => x.status === 'waiting'))) {
      q.status = 'uploading'; q.progress = 0; q.error = null; render();
      const fd = new FormData();
      fd.append('file', q.file);
      Object.entries(q.meta).forEach(([k, v]) => fd.append(k, v));
      try {
        q.req = api.upload('/api/uploads', fd, (p) => { q.progress = p; if (p >= 1) q.status = 'processing'; render(); });
        const r = await q.req;
        q.status = 'completed'; q.video = r.video;
        ui.toast(`Uploaded “${r.video.title}”`);
        recent();
      } catch (e) {
        q.status = 'failed'; q.error = e.message;
        q.retryable = ![413, 415, 422].includes(e.status);
      }
      render();
    }
    running = false;
  }

  function add(files) {
    const multi = files.length > 1;
    for (const file of files) {
      const meta = { title: multi ? '' : $('#title').value.trim(), description: $('#description').value, tags: $('#tags').value, category: $('#category').value };
      const err = validate(file);
      queue.push({ file, meta, status: err ? 'failed' : 'waiting', error: err, progress: 0, retryable: false });
      if (err) ui.toast(`${file.name}: ${err}`, 'bad');
    }
    $('#title').value = '';
    render(); pump();
  }

  $('#file').addEventListener('change', (e) => { add(Array.from(e.target.files)); e.target.value = ''; });
  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => add(Array.from(e.dataTransfer.files)));
  $('#queue').addEventListener('click', (e) => {
    const r = e.target.closest('[data-retry]');
    if (r) { queue[r.dataset.retry].status = 'waiting'; render(); pump(); }
    const a = e.target.closest('[data-abort]');
    if (a) { const q = queue[a.dataset.abort]; q.req && q.req.abort(); }
  });
  window.addEventListener('beforeunload', (e) => { if (queue.some((q) => ['uploading', 'waiting'].includes(q.status))) { e.preventDefault(); e.returnValue = ''; } });

  async function recent() {
    try {
      const d = await api.get('/api/videos?filter=uploaded&per_page=6');
      $('#recent').innerHTML = d.items.length ? d.items.map((v) => `<li>
        ${v.has_thumbnail ? `<img class="thumb-sm" src="/media/thumb/${v.id}" alt="">` : `<span class="thumb-sm">${icon('upload')}</span>`}
        <div class="main-col"><a class="t" href="/videos/${v.id}">${esc(v.title)}</a><div class="subtle">${fmt.bytes(v.file_size)} · ${fmt.dur(v.duration)}</div></div></li>`).join('')
        : `<li style="display:block">${ui.empty('upload', 'Nothing uploaded yet', 'Your uploads will be listed here.')}</li>`;
    } catch (e) { /* ignore */ }
  }
  recent();
})();
