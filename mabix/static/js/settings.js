(function () {
  const { $, esc, icon, fmt } = ui;
  let s = null;

  const dl = (rows) => rows.map(([k, v]) => `<dt class="subtle">${esc(k)}</dt><dd style="margin:0;word-break:break-all">${v}</dd>`).join('');
  const cap = (ok, label, detail) => `<div class="cap ${ok ? 'ok' : 'no'}">${icon(ok ? 'check-circle' : 'alert')}<b>${esc(label)}</b><span>${esc(detail)}</span></div>`;

  function render() {
    $('#profile-info').innerHTML = dl([['Username', esc(s.profile.username)], ['Role', 'Administrator'], ['Credentials from', esc(s.profile.credentials_source)],
      ['Session length', `${s.profile.session_hours} hours`], ['Environment', esc(s.profile.environment)]]);
    const m = s.models;
    $('#m-video_engine').innerHTML = Object.entries(s.video_engines).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    Object.entries(m).forEach(([k, v]) => { const el = $('#m-' + k); if (el) el.value = v; });
    const d = s.defaults;
    $('#d-voice').innerHTML = Object.entries(s.voices).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    ['aspect', 'resolution', 'motion', 'voice', 'transition'].forEach((k) => { $('#d-' + k).value = d[k]; });
    $('#d-subsize').value = d.subtitles.size; $('#d-subpos').value = d.subtitles.position;
    $('#d-subs').checked = d.subtitles.enabled !== false; $('#d-review').checked = d.review !== false;
    $('#api-keys').innerHTML = cap(s.api.pexels, 'Pexels', s.api.pexels ? 'configured' : 'not set') + cap(s.api.pixabay, 'Pixabay', s.api.pixabay ? 'configured' : 'not set');
    $('#pub').innerHTML = cap(s.publishing.youtube, 'YouTube', s.publishing.youtube ? `connected (${s.publishing.youtube_token_file})` : `${s.publishing.youtube_token_file} not found`);
    $('#storage-info').innerHTML = dl([['Media folder', `<span class="mono">${esc(s.storage.data_dir)}</span>`], ['News videos folder', `<span class="mono">${esc(s.storage.legacy_dir)}</span>`],
      ['Space used', fmt.bytes(s.storage.used_bytes)], ['Max upload size', `${s.storage.max_upload_mb} MB`]]);
    const theme = (() => { try { return localStorage.getItem('mabix-theme'); } catch (e) { return null; } })() || s.preferences.theme || 'system';
    const r = document.querySelector(`input[name=theme][value="${theme}"]`); if (r) r.checked = true;
  }

  async function load() {
    try { s = await api.get('/api/settings'); render(); music(); } catch (e) { ui.toast(e.message, 'bad'); }
  }

  async function caps(refresh) {
    try {
      const c = await api.get('/api/capabilities' + (refresh ? '?refresh=1' : ''));
      $('#caps').innerHTML = Object.values(c).map((x) => cap(x.ok, x.label, x.detail)).join('');
    } catch (e) { $('#caps').innerHTML = `<p class="subtle">${esc(e.message)}</p>`; }
  }

  async function music(items) {
    try {
      items = items || (await api.get('/api/music')).items;
      $('#music-list').innerHTML = items.length ? items.map((t) => `<li>${icon('music')}<div class="main-col"><div class="t">${esc(t.name)}</div><div class="subtle">${fmt.bytes(t.size)}</div></div>
        <button class="btn ghost sm icon-only danger" data-del="${esc(t.name)}" aria-label="Delete track">${icon('trash', 'sm')}</button></li>`).join('')
        : `<li style="display:block">${ui.empty('music', 'No music tracks yet', 'Add MP3/WAV tracks to use as background music.')}</li>`;
      const sel = $('#d-music');
      sel.innerHTML = '<option value="">None</option>' + items.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join('');
      if (s) sel.value = s.defaults.music || '';
    } catch (e) { ui.toast(e.message, 'bad'); }
  }

  const formObj = (f) => Object.fromEntries(new FormData(f).entries());

  $('#models-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try { s = await api.put('/api/settings/models', formObj(e.target)); render(); ui.toast('Model settings saved. Models reload on the next job.'); caps(true); }
    catch (er) { ui.toast(er.message, 'bad'); }
  });
  $('#defaults-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = { aspect: $('#d-aspect').value, resolution: $('#d-resolution').value, motion: $('#d-motion').value, voice: $('#d-voice').value,
      transition: $('#d-transition').value, music: $('#d-music').value, review: $('#d-review').checked ? '1' : '0',
      subtitles: { enabled: $('#d-subs').checked, size: $('#d-subsize').value, position: $('#d-subpos').value } };
    try { s = await api.put('/api/settings/defaults', body); render(); ui.toast('Defaults saved'); } catch (er) { ui.toast(er.message, 'bad'); }
  });
  $('#prefs-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const theme = document.querySelector('input[name=theme]:checked').value;
    try {
      s = await api.put('/api/settings/preferences', { theme });
      try { if (theme === 'system') localStorage.removeItem('mabix-theme'); else localStorage.setItem('mabix-theme', theme); } catch (er) { /* ignore */ }
      if (theme === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', theme);
      ui.toast('Preferences saved');
    } catch (er) { ui.toast(er.message, 'bad'); }
  });
  $('#pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if ($('#pw-new').value !== $('#pw-new2').value) { ui.toast('New passwords do not match', 'bad'); return; }
    try { await api.post('/api/settings/password', { current: $('#pw-cur').value, new: $('#pw-new').value }); e.target.reset(); ui.toast('Password updated'); load(); }
    catch (er) { ui.toast(er.message, 'bad'); }
  });
  $('#music-file').addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try { const r = await api.upload('/api/music', fd); music(r.items); ui.toast('Track added'); } catch (er) { ui.toast(er.message, 'bad'); }
    e.target.value = '';
  });
  $('#music-list').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-del]'); if (!b) return;
    if (!(await ui.confirmDialog({ title: 'Delete track?', message: `“${b.dataset.del}” will be removed from the music library.`, confirmText: 'Delete', danger: true }))) return;
    try { const r = await api.del('/api/music/' + encodeURIComponent(b.dataset.del)); music(r.items); ui.toast('Track deleted'); } catch (er) { ui.toast(er.message, 'bad'); }
  });
  $('#caps-refresh').addEventListener('click', () => caps(true));

  load(); caps(false);
})();
