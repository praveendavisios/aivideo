(function () {
  const { $, esc, icon } = ui;
  const form = $('#studio-form');
  const module = form.dataset.module;
  let opts = null;

  const fill = (sel, entries, value) => {
    sel.innerHTML = entries.map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('');
    if (value !== undefined && sel.querySelector(`option[value="${CSS.escape(String(value))}"]`)) sel.value = value;
  };

  function motionHint() {
    const m = $('#motion').value;
    const hint = { t2v: 'Each scene is generated as an AI video clip on your GPU (engine set in Settings).',
      i2v: 'An AI keyframe is generated per scene, then animated with Stable Video Diffusion.',
      none: 'Faster: AI keyframe images with cinematic camera motion (no AI motion).' }[m];
    $('#motion-hint').textContent = hint;
    const i2vHint = $('#i2v-hint');
    if (i2vHint) i2vHint.textContent = opts.svd_configured
      ? 'Uploaded images are animated into AI video with Stable Video Diffusion (one per scene, in order).'
      : 'Stable Video Diffusion is not configured, so uploaded images get camera motion only. Enable it in Settings → AI models for true image-to-video.';
  }

  async function init() {
    try {
      opts = await api.get('/api/studio-options');
      const d = opts.defaults;
      fill($('#motion'), Object.entries(opts.motion).filter(([k]) => k !== 'i2v' || opts.svd_configured), d.motion);
      fill($('#voice'), Object.entries(opts.voices), d.voice);
      fill($('#transition'), Object.entries(opts.transitions), d.transition);
      fill($('#music'), [['', 'None'], ...opts.music.map((m) => [m.name, m.name])], d.music);
      $('#resolution').value = d.resolution;
      $('#music_volume').value = d.music_volume;
      const a = form.querySelector(`input[name=aspect][value="${d.aspect}"]`); if (a) a.checked = true;
      form.review.checked = d.review !== false;
      ['size', 'position', 'color'].forEach((k) => { const el = $('#subtitles_' + k); if (d.subtitles[k] && el.querySelector(`option[value="${CSS.escape(d.subtitles[k])}"]`)) el.value = d.subtitles[k]; });
      form.subtitles_enabled.checked = d.subtitles.enabled !== false;
      if (!opts.music.length) $('#music').title = 'Upload tracks in Settings → Music library';
      motionHint(); volOut();
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  async function readiness() {
    try {
      const c = await api.get('/api/capabilities');
      const needed = ['llm', 'tts', 'render', 'gpu', 'video'];
      const missing = needed.filter((k) => !c[k].ok).map((k) => c[k]);
      $('#readiness').innerHTML = missing.length ? `<div class="notice warn" style="margin-bottom:18px">${icon('alert')}<div>
        <b>Some AI services are not ready on this server — generation will fail until they are available:</b>
        <ul>${missing.map((m) => `<li>${esc(m.label)}: ${esc(m.detail)}</li>`).join('')}</ul></div></div>` : '';
    } catch (e) { /* non-blocking */ }
  }

  async function recent() {
    try {
      const d = await api.get(`/api/videos?type=${module}&per_page=6`);
      $('#recent').innerHTML = d.items.length ? d.items.map((v) => `<li>
        ${v.has_thumbnail ? `<img class="thumb-sm" src="/media/thumb/${v.id}" alt="">` : `<span class="thumb-sm">${icon('library')}</span>`}
        <div class="main-col"><a class="t" href="/videos/${v.id}">${esc(v.title || v.prompt)}</a><div class="subtle">${ui.fmt.ago(v.created_at)}</div></div>
        ${ui.badge(v.status)}</li>`).join('')
        : `<li style="display:block">${ui.empty('sparkles', 'No projects yet', 'Your generated videos will appear here.')}</li>`;
    } catch (e) { /* ignore */ }
  }

  const volOut = () => { $('#vol-out').textContent = Math.round($('#music_volume').value * 100) + '%'; };
  $('#music_volume').addEventListener('input', volOut);
  $('#motion').addEventListener('change', motionHint);

  const imgs = $('#images');
  if (imgs) imgs.addEventListener('change', () => {
    $('#img-preview').innerHTML = '';
    Array.from(imgs.files).slice(0, 8).forEach((f) => {
      const im = document.createElement('img'); im.alt = f.name; im.src = URL.createObjectURL(f); $('#img-preview').appendChild(im);
    });
  });

  let submitting = false;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submitting) return;
    const prompt = form.prompt.value.trim();
    if (prompt.length < 3) { ui.toast('Please enter a prompt first.', 'bad'); form.prompt.focus(); return; }
    const fd = new FormData(form);
    fd.set('review', form.review.checked ? '1' : '0');
    fd.set('subtitles_enabled', form.subtitles_enabled.checked ? '1' : '0');
    fd.set('slow_voice', form.slow_voice.checked ? '1' : '0');
    submitting = true; ui.busy($('#submit'), true, 'Starting…');
    try {
      const r = await api.upload(`/api/studio/${module}`, fd);
      ui.toast(form.review.checked ? 'Writing your script — you can review it shortly.' : 'Generation started — the AI video will render automatically.');
      location.href = `/videos/${r.video_id}`;
    } catch (err) {
      if (err.status === 409 && err.data.video_id) { ui.toast(err.message, 'info'); location.href = `/videos/${err.data.video_id}`; return; }
      ui.toast(err.message, 'bad'); submitting = false; ui.busy($('#submit'), false);
    }
  });

  init(); readiness(); recent();
})();
