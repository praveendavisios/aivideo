(function () {
  const { $, $$, esc, icon, fmt, badge, TYPE_LABEL, KIND } = ui;
  const root = $('#vp');
  const id = root.dataset.id;
  let video = null, jobs = [], dirty = false, scenes = [], pollTimer = null, lastActiveId = null;
  const ACTIVE = ['pending', 'queued', 'processing'];
  const activeJob = () => jobs.find((j) => ACTIVE.includes(j.status));

  /* ---------- rendering ---------- */
  function renderHead() {
    $('#v-title').textContent = video.title || video.prompt || 'Untitled';
    document.title = `${video.title || 'Video'} · Mabix AI`;
    $('#head-badges').innerHTML = `<span class="badge ${video.source_type === 'upload' ? 'good' : 'brand'}">${TYPE_LABEL[video.video_type]}</span>
      ${badge(video.status)}${video.publish_status !== 'unpublished' ? badge(video.publish_status) : ''}`;
    $('#v-sub').textContent = video.prompt && video.source_type === 'ai' ? `Prompt: ${video.prompt}` : '';
    const done = video.status === 'completed' && video.has_file;
    const canRegen = video.source_type === 'ai' && (video.script || video.config.mode === 'quick') && !activeJob();
    $('#head-actions').innerHTML = `
      ${done ? `<a class="btn" href="/media/video/${id}?download=1">${icon('download', 'sm')} Download</a>` : ''}
      ${canRegen && video.status !== 'review' ? `<button class="btn" data-act="regenerate">${icon('refresh', 'sm')} Regenerate</button>` : ''}
      <button class="btn danger" data-act="delete">${icon('trash', 'sm')} Delete</button>`;
  }

  function renderSteps() {
    if (video.source_type !== 'ai' || video.config.mode === 'quick') { $('#steps').hidden = true; return; }
    const s = video.status;
    const idx = { scripting: 2, review: 3, storyboarding: 3, queued: 4, processing: 4, completed: 6, failed: video.script ? 3 : 2 }[s] ?? 2;
    $('#steps').hidden = false;
    $('#steps').innerHTML = ['Input', 'Configure', 'AI script', 'Review & edit', 'AI video render', 'Preview & publish']
      .map((l, i) => `<li class="${i < idx ? 'done' : i === idx ? 'now' : ''}">${l}</li>`).join('');
  }

  function renderBanner() {
    const j = activeJob();
    if (j) {
      $('#job-banner').innerHTML = `<div class="card card-pad" style="margin-bottom:18px"><div class="row">
        ${icon('loader', 'spin')}<div style="flex:1;min-width:200px"><b>${KIND[j.kind] || j.kind} — ${esc(j.status === 'processing' ? j.stage || 'Working…' : j.status === 'pending' ? 'Waiting for the previous step' : 'Queued, waiting for the GPU worker')}</b>
        <div class="progress lg" style="margin-top:8px"><i style="width:${j.progress}%"></i></div></div>
        <span class="subtle">${j.progress}%</span>
        <button class="btn sm" data-cancel="${j.id}" ${j.cancel_requested ? 'disabled' : ''}>${icon('stop', 'sm')} ${j.cancel_requested ? 'Cancelling…' : 'Cancel'}</button></div></div>`;
    } else if (video.status === 'failed' || video.error) {
      const failed = jobs.find((x) => x.status === 'failed');
      $('#job-banner').innerHTML = `<div class="notice bad" style="margin-bottom:18px">${icon('alert')}<div style="flex:1"><b>${video.status === 'failed' ? 'Generation failed' : 'Last step failed'}</b>
        <div class="mono" style="margin-top:4px;white-space:pre-wrap">${esc(video.error || (failed && failed.error) || 'Unknown error')}</div></div>
        ${failed ? `<button class="btn sm" data-retry="${failed.id}">${icon('refresh', 'sm')} Retry</button>` : ''}</div>`;
    } else if (video.status === 'review') {
      $('#job-banner').innerHTML = `<div class="notice info" style="margin-bottom:18px">${icon('info')}<div><b>Script ready for review.</b> Edit the scenes below, optionally preview a storyboard, then click <b>Generate AI video</b>.</div></div>`;
    } else $('#job-banner').innerHTML = '';
  }

  function renderPlayer() {
    const show = video.status === 'completed' && video.has_file;
    $('#player-card').hidden = !show;
    const p = $('#player');
    const src = `/media/video/${id}?v=${encodeURIComponent(video.updated_at)}`;
    if (show && p.getAttribute('src') !== src) p.setAttribute('src', src);
  }

  function shotClass() { return { '16:9': 'r169', '1:1': 'r11' }[video.config.aspect] || ''; }

  function sceneHtml(s, i) {
    const shot = s.has_clip ? `<video src="/media/scene/${id}/${s.source_index}/clip?v=${s.v}" muted loop playsinline autoplay></video>`
      : s.has_image ? `<img src="/media/scene/${id}/${s.source_index}/image?v=${s.v}" alt="Scene ${i + 1} keyframe">`
      : `<span>${icon('image', 'lg')}<br>${s.has_input_image ? 'Uploaded image' : 'No keyframe yet'}</span>`;
    const lock = !!activeJob();
    return `<div class="scene" data-i="${i}">
      <div class="stack" style="gap:8px">
        <div class="shot ${shotClass()}">${shot}</div>
        ${s.visual_kind ? `<span class="subtle" style="font-size:11.5px">${esc(s.visual_kind)}</span>` : ''}
        ${s.source_index != null && !s.new && video.video_type !== 'news' ? `<button class="btn sm" data-kf="${i}" ${lock ? 'disabled' : ''}>${icon('refresh', 'sm')} Keyframe</button>` : ''}
      </div>
      <div class="stack" style="gap:10px">
        <div class="row"><span class="num">SCENE ${i + 1}</span><span class="spacer"></span>
          <button class="btn ghost sm icon-only" data-up="${i}" aria-label="Move up" ${i === 0 ? 'disabled' : ''} style="transform:rotate(180deg)">${icon('chev', 'sm')}</button>
          <button class="btn ghost sm icon-only" data-down="${i}" aria-label="Move down" ${i === scenes.length - 1 ? 'disabled' : ''}>${icon('chev', 'sm')}</button>
          <button class="btn ghost sm icon-only danger" data-rm="${i}" aria-label="Remove scene" ${scenes.length <= 1 ? 'disabled' : ''}>${icon('trash', 'sm')}</button></div>
        <div class="field"><label>Narration</label><textarea data-f="narration" rows="2">${esc(s.narration)}</textarea></div>
        <div class="field"><label>Visual prompt (what the AI video shows)</label><textarea data-f="visual" rows="2">${esc(s.visual)}</textarea></div>
        <div class="form-grid" style="grid-template-columns:1fr 1fr">
          <div class="field"><label>On-screen heading</label><input type="text" data-f="heading" value="${esc(s.heading)}" maxlength="60"></div>
          <div class="field"><label>Highlight keywords</label><input type="text" data-f="keywords" value="${esc((s.keywords || []).join(', '))}" placeholder="comma separated"></div>
        </div>
      </div></div>`;
  }

  function renderScript() {
    const sc = video.script;
    const show = video.source_type === 'ai' && !!sc;
    $('#script-card').hidden = !show;
    if (!show) return;
    if (!dirty) {
      $('#s-title').value = video.title || sc.title || '';
      $('#s-desc').value = video.description || '';
      $('#s-chars').value = sc.characters || '';
      scenes = sc.scenes.map((s, i) => ({ ...s, source_index: i }));
    }
    $('#chars-field').hidden = video.video_type === 'news';
    $('#scenes').innerHTML = scenes.map(sceneHtml).join('');
    const lock = !!activeJob();
    ['#save-script', '#regen-script', '#storyboard', '#render', '#add-scene'].forEach((s) => { $(s).disabled = lock; });
    $('#render').innerHTML = `${icon('film', 'sm')} ${video.status === 'completed' ? 'Re-render video' : 'Generate AI video'}`;
    $('#storyboard').innerHTML = `${icon('layers', 'sm')} ${video.video_type === 'news' ? 'Fetch scene images' : 'Generate storyboard'}`;
    $('#dirty-note').hidden = !dirty;
  }

  function renderDetails() {
    const c = video.config || {};
    const rows = [
      ['Type', TYPE_LABEL[video.video_type]], ['Source', video.source_type === 'upload' ? 'Direct upload' : 'AI generated'],
      ['Created', fmt.date(video.created_at)], ['Completed', video.completed_at ? fmt.date(video.completed_at) : '—'],
      ['Duration', video.duration ? fmt.dur(video.duration) : '—'], ['Resolution', fmt.res(video) || '—'], ['File size', fmt.bytes(video.file_size)],
    ];
    if (video.source_type === 'ai') {
      if (c.mode === 'quick') rows.push(['Pipeline', 'Original news pipeline'], ['Format', c.format], ['Images', c.images_used || (c.ai_only ? 'AI only' : 'Stock + AI')]);
      else rows.push(['Aspect', c.aspect], ['Style', c.style || '—'], ['Visuals', { t2v: 'AI video (text-to-video)', i2v: 'AI video (image-to-video)', none: 'AI images + camera motion' }[c.motion] || '—'],
        ['Voice', c.voice], ['Music', c.music || 'None'], ['Target length', c.duration ? c.duration + 's' : '—']);
    } else rows.push(['Original file', c.original_filename || '—']);
    if (video.category) rows.push(['Category', video.category]);
    if (video.tags) rows.push(['Tags', video.tags]);
    $('#details').innerHTML = rows.map(([k, v]) => `<dt class="subtle">${esc(k)}</dt><dd style="margin:0;word-break:break-word">${esc(v)}</dd>`).join('');
  }

  function renderPublish() {
    const b = $('#publish-body');
    if (video.publish_status === 'published') {
      b.innerHTML = `<p>${badge('published')}</p><p style="margin-top:10px"><a href="${esc(video.publish_url)}" target="_blank" rel="noopener">${icon('external', 'sm')} View on YouTube</a></p>`;
    } else if (video.publish_status === 'publishing') {
      b.innerHTML = `<p>${badge('publishing')}</p><p class="subtle" style="margin-top:8px">Uploading to YouTube…</p>`;
    } else if (video.status === 'completed') {
      b.innerHTML = `${video.publish_status === 'failed' ? `<div class="notice bad" style="margin-bottom:12px">${icon('alert')}<div>Last publish failed. See the job log.</div></div>` : ''}
        <p class="subtle" style="margin-bottom:12px">Publish using your connected YouTube channel.</p>
        <button class="btn primary" data-act="publish">${icon('send', 'sm')} Publish to YouTube</button>`;
    } else b.innerHTML = '<p class="subtle">Available once the video is completed.</p>';
  }

  function renderJobs() {
    $('#jobs').innerHTML = jobs.length ? jobs.map((j) => `<li style="flex-wrap:wrap">
      <div class="main-col"><div class="t">${KIND[j.kind] || j.kind} <span class="subtle">#${j.id}</span></div>
      <div class="subtle">${fmt.ago(j.created_at)}${j.finished_at ? ' · ' + fmt.elapsed(j.started_at, j.finished_at) : ''}</div></div>
      ${badge(j.status)}<button class="btn ghost sm icon-only" data-log="${j.id}" aria-label="View log">${icon('eye', 'sm')}</button></li>`).join('')
      : `<li class="subtle">No jobs.</li>`;
  }

  function renderAll() { renderHead(); renderSteps(); renderBanner(); renderPlayer(); renderScript(); renderDetails(); renderPublish(); renderJobs(); }

  /* ---------- data ---------- */
  async function load() {
    try {
      const d = await api.get(`/api/videos/${id}`);
      video = d.video; jobs = d.jobs;
      const a = activeJob();
      const finished = lastActiveId && (!a || a.id !== lastActiveId);
      if (finished && !dirty) scenes = [];
      lastActiveId = a ? a.id : null;
      renderAll();
      if (finished) {
        const j = jobs.find((x) => x.id !== undefined);
        if (video.status === 'completed' && j && j.kind !== 'publish') ui.toast('Your video is ready!');
      }
      clearTimeout(pollTimer);
      if (a || video.publish_status === 'publishing') pollTimer = setTimeout(load, 2000);
    } catch (e) {
      if (e.status === 404) { ui.toast('This video no longer exists', 'bad'); location.href = '/library'; return; }
      ui.toast(e.message, 'bad'); clearTimeout(pollTimer); pollTimer = setTimeout(load, 5000);
    }
  }

  function collect() {
    $$('.scene').forEach((el) => {
      const s = scenes[+el.dataset.i];
      $$('[data-f]', el).forEach((f) => {
        s[f.dataset.f] = f.dataset.f === 'keywords' ? f.value.split(',').map((k) => k.trim()).filter(Boolean) : f.value;
      });
    });
    return {
      title: $('#s-title').value.trim(), description: $('#s-desc').value, characters: $('#s-chars').value,
      scenes: scenes.map((s) => ({ narration: s.narration, visual: s.visual, heading: s.heading, keywords: s.keywords, source_index: s.new ? null : s.source_index })),
    };
  }

  async function save(quiet) {
    const body = collect();
    if (!body.title) { ui.toast('Title is required', 'bad'); return false; }
    if (body.scenes.some((s) => !s.narration.trim())) { ui.toast('Every scene needs narration text', 'bad'); return false; }
    try {
      const d = await api.put(`/api/videos/${id}/script`, body);
      video = d.video; dirty = false; scenes = []; renderAll();
      if (!quiet) ui.toast('Script saved');
      return true;
    } catch (e) { ui.toast(e.message, 'bad'); return false; }
  }

  async function startJob(url, body, msg) {
    try { await api.post(url, body); ui.toast(msg, 'info'); await load(); }
    catch (e) { ui.toast(e.message, 'bad'); if (e.data && e.data.job_id) load(); }
  }

  /* ---------- events ---------- */
  root.addEventListener('input', (e) => { if (e.target.closest('#script-card')) { dirty = true; $('#dirty-note').hidden = false; } });
  window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  root.addEventListener('click', async (e) => {
    const t = e.target.closest('button, [data-act]');
    if (!t) return;
    const d = t.dataset;
    if (d.up || d.down || d.rm) {
      collect();
      const i = +(d.up ?? d.down ?? d.rm);
      if (d.up) [scenes[i - 1], scenes[i]] = [scenes[i], scenes[i - 1]];
      else if (d.down) [scenes[i + 1], scenes[i]] = [scenes[i], scenes[i + 1]];
      else scenes.splice(i, 1);
      dirty = true; renderScript(); return;
    }
    if (d.kf !== undefined) {
      if (dirty && !(await save(true))) return;
      const s = video.script.scenes[+d.kf] ? +d.kf : null;
      if (s === null) return;
      return startJob(`/api/videos/${id}/storyboard`, { scene: s }, `Regenerating keyframe for scene ${s + 1}…`);
    }
    if (d.cancel) {
      if (await ui.confirmDialog({ title: 'Cancel this job?', message: 'The job stops at the next safe point. Progress for completed scenes is kept.', confirmText: 'Cancel job', danger: true })) {
        try { await api.post(`/api/jobs/${d.cancel}/cancel`); ui.toast('Cancellation requested', 'info'); load(); } catch (er) { ui.toast(er.message, 'bad'); }
      }
      return;
    }
    if (d.retry) { try { await api.post(`/api/jobs/${d.retry}/retry`); ui.toast('Retrying…', 'info'); load(); } catch (er) { ui.toast(er.message, 'bad'); } return; }
    if (d.log) return showLog(d.log);
    if (d.act === 'delete') { if (await ui.deleteVideo(id, video.title)) { dirty = false; location.href = '/library'; } return; }
    if (d.act === 'regenerate') {
      if (await ui.confirmDialog({ title: 'Regenerate video?', message: video.config.mode === 'quick' ? 'The news pipeline runs again for this article.' : 'All scene AI videos are generated again with new results, then the video is re-rendered.', confirmText: 'Regenerate' }))
        startJob(`/api/videos/${id}/regenerate`, {}, 'Regeneration queued');
      return;
    }
    if (d.act === 'publish') return publishDialog();
    switch (t.id) {
      case 'add-scene': collect(); scenes.push({ narration: '', visual: '', heading: '', keywords: [], new: true }); dirty = true; renderScript(); $$('.scene').pop().querySelector('textarea').focus(); break;
      case 'save-script': save(); break;
      case 'regen-script':
        if (await ui.confirmDialog({ title: 'Rewrite the script?', message: 'The AI writes a new script from your original prompt. Your current edits will be replaced.', confirmText: 'Rewrite' })) {
          dirty = false; scenes = []; startJob(`/api/videos/${id}/script/regenerate`, {}, 'Rewriting script…');
        }
        break;
      case 'storyboard': if (!dirty || await save(true)) startJob(`/api/videos/${id}/storyboard`, {}, 'Generating storyboard keyframes…'); break;
      case 'render': if (!dirty || await save(true)) startJob(`/api/videos/${id}/render`, {}, 'AI video generation queued'); break;
      case 'edit-meta': metaDialog(); break;
      default: break;
    }
  });

  async function showLog(jobId) {
    try {
      const { job } = await api.get(`/api/jobs/${jobId}`);
      ui.modal({ title: `${KIND[job.kind] || job.kind} job #${job.id}`, wide: true,
        body: `<div class="row" style="margin-bottom:12px">${badge(job.status)}<span class="subtle">Created ${fmt.date(job.created_at)} · Duration ${fmt.elapsed(job.started_at, job.finished_at)}</span></div>
          ${job.error ? `<div class="notice bad" style="margin-bottom:12px">${icon('alert')}<div class="mono">${esc(job.error)}</div></div>` : ''}<pre class="log">${esc(job.log)}</pre>` });
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  function metaDialog() {
    const m = ui.modal({ title: 'Edit metadata', body: `<div class="stack">
      <div class="field"><label for="m-title">Title</label><input type="text" id="m-title" maxlength="200" value="${esc(video.title)}"></div>
      <div class="field"><label for="m-desc">Description</label><textarea id="m-desc" rows="4">${esc(video.description)}</textarea></div>
      <div class="field"><label for="m-tags">Tags</label><input type="text" id="m-tags" value="${esc(video.tags)}" placeholder="comma separated"></div>
      <div class="field"><label for="m-cat">Category</label><input type="text" id="m-cat" value="${esc(video.category)}"></div></div>`,
      footer: `<button class="btn" data-x>Cancel</button><button class="btn primary" id="m-save">Save</button>` });
    $('#m-save', m.el).addEventListener('click', async () => {
      try {
        const d = await api.patch(`/api/videos/${id}`, { title: $('#m-title', m.el).value, description: $('#m-desc', m.el).value, tags: $('#m-tags', m.el).value, category: $('#m-cat', m.el).value });
        video = d.video; dirty = false; scenes = []; renderAll(); m.close(); ui.toast('Metadata updated');
      } catch (e) { ui.toast(e.message, 'bad'); }
    });
  }

  function publishDialog() {
    const vertical = video.height > video.width;
    const m = ui.modal({ title: 'Publish to YouTube', body: `<div class="stack">
      <div class="field"><label for="p-title">Title</label><input type="text" id="p-title" maxlength="100" value="${esc(video.title)}"></div>
      <div class="field"><label for="p-desc">Description</label><textarea id="p-desc" rows="4">${esc(video.description)}</textarea></div>
      <div class="field"><label for="p-tags">Tags</label><input type="text" id="p-tags" value="${esc(video.tags || 'news, ai, shorts')}"></div>
      <div class="form-grid"><div class="field"><label for="p-priv">Visibility</label><select id="p-priv"><option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select></div>
      <div class="field" style="justify-content:flex-end"><label class="check"><input type="checkbox" id="p-shorts" ${vertical ? 'checked' : ''}> Add #shorts</label></div></div></div>`,
      footer: `<button class="btn" data-x>Cancel</button><button class="btn primary" id="p-go">${icon('send', 'sm')} Publish</button>` });
    $('#p-go', m.el).addEventListener('click', async (e) => {
      ui.busy(e.currentTarget, true, 'Queuing…');
      try {
        await api.post(`/api/videos/${id}/publish`, { title: $('#p-title', m.el).value, description: $('#p-desc', m.el).value, tags: $('#p-tags', m.el).value, privacy: $('#p-priv', m.el).value, shorts: $('#p-shorts', m.el).checked });
        m.close(); ui.toast('Publishing to YouTube…', 'info'); load();
      } catch (er) { ui.toast(er.message, 'bad'); ui.busy(e.currentTarget, false); }
    });
  }

  load();
})();
