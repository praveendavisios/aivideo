(() => {
  const { $, esc, api, toast } = MC;
  let curId = null, nodeId = null;
  const roleTabs = { admin: ['teacher', 'parent', 'admin'], teacher: ['teacher'], parent: ['parent', 'teacher'] };

  function show(tab) {
    ['teacher', 'parent', 'admin'].forEach((t) => $('#' + t).classList.toggle('hidden', t !== tab));
    document.querySelectorAll('#tabs a[data-tab]').forEach((a) => a.classList.toggle('on', a.dataset.tab === tab));
    ({ teacher: loadTeacher, parent: loadParent, admin: loadAdmin })[tab]();
  }
  function start(user) {
    $('#loginCard').classList.add('hidden'); $('#who').textContent = `${user.email} · ${user.role}`; $('#logout').classList.remove('hidden');
    roleTabs[user.role].forEach((t) => $('#tabs').insertAdjacentHTML('afterbegin', `<a href="#" data-tab="${t}">${t === 'teacher' ? (user.role === 'parent' ? 'Curricula' : 'Teacher') : t[0].toUpperCase() + t.slice(1)}</a>`));
    document.querySelectorAll('#tabs a[data-tab]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); show(a.dataset.tab); }; });
    show(roleTabs[user.role][0]);
  }
  $('#loginBtn').onclick = async () => { try { start(await MC.login($('#email').value, $('#pw').value)); } catch (e) { toast(e.message); } };
  $('#pw').onkeydown = (e) => e.key === 'Enter' && $('#loginBtn').click();
  $('#logout').onclick = () => { MC.logout(); location.reload(); };
  if (MC.token() && MC.user()) api('/api/v1/auth/me').then(start).catch(() => MC.logout());

  /* ------------------------------ teacher ------------------------------ */
  async function loadTeacher() {
    const curs = await api('/api/v1/curricula');
    $('#curList').innerHTML = curs.map((c) => `<tr><td><a href="#" data-cur="${c.id}"><b>${esc(c.title)}</b></a><div class="faint">${esc(c.subject)} · grade ${esc(c.grade || '?')} · ${c.documents.length} file(s)</div>
      ${c.documents.map((d) => `<div class="faint">${esc(d.filename)} — <span class="pill ${d.status === 'ready' ? 'good' : d.status === 'failed' ? 'bad' : 'warn'}">${d.status}</span> ${d.pages || ''} p. ${d.method || ''} ${d.error ? '· ' + esc(d.error) : ''}</div>`).join('')}</td>
      <td><span class="pill ${c.status === 'ready' ? 'good' : c.status === 'failed' ? 'bad' : 'warn'}">${c.status}</span></td><td><button class="sm danger" data-del="${c.id}">Delete</button></td></tr>`).join('') || '<tr><td class="faint">No curricula yet.</td></tr>';
    $('#curList').querySelectorAll('[data-cur]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); openCur(a.dataset.cur); }; });
    $('#curList').querySelectorAll('[data-del]').forEach((b) => { b.onclick = async () => { if (confirm('Delete this curriculum and its files?')) { await api('/api/v1/curricula/' + b.dataset.del, { method: 'DELETE' }); loadTeacher(); } }; });
    $('#assignCur').innerHTML = curs.map((c) => `<option value="${c.id}">${esc(c.title)}</option>`).join('');
    const stus = await api('/api/v1/students');
    $('#assignStu').innerHTML = stus.map((s) => `<option value="${s.id}">${esc(s.display_name)}</option>`).join('');
    if (curs.some((c) => c.status === 'processing')) setTimeout(loadTeacher, 2000);
    if (MC.user().role !== 'parent') loadClasses();
  }
  $('#uploadBtn').onclick = async () => {
    const files = $('#cFiles').files; if (!files.length || !$('#cTitle').value) return toast('Add a title and at least one file');
    const fd = new FormData(); fd.append('title', $('#cTitle').value); fd.append('subject', $('#cSubject').value); fd.append('grade', $('#cGrade').value); fd.append('language', $('#cLang').value);
    [...files].forEach((f) => fd.append('files', f));
    try { await api('/api/v1/curricula', { body: fd }); toast('Uploaded — analysing in the background'); $('#cFiles').value = ''; loadTeacher(); } catch (e) { toast(e.message); }
  };
  async function openCur(id) {
    curId = id; const c = await api('/api/v1/curricula/' + id);
    const render = (nodes) => `<ul class="tree">${nodes.map((n) => `<li><span class="node" data-node="${n.id}">${esc(n.title)}</span>
      <span class="faint">${n.level}${n.counts.questions ? ` · ${n.counts.questions} Q` : ''}${n.counts.definitions ? ` · ${n.counts.definitions} def` : ''}</span>${n.children.length ? render(n.children) : ''}</li>`).join('')}</ul>`;
    $('#treeBox').innerHTML = render(c.tree) || '<span class="faint">Nothing extracted yet.</span>';
    $('#treeBox').querySelectorAll('[data-node]').forEach((s) => { s.onclick = () => { document.querySelectorAll('.node.on').forEach((x) => x.classList.remove('on')); s.classList.add('on'); openNode(s.dataset.node); }; });
  }
  $('#searchQ').onkeydown = async (e) => {
    if (e.key !== 'Enter' || !curId) return;
    const r = await api(`/api/v1/curricula/${curId}/search?q=${encodeURIComponent($('#searchQ').value)}`);
    $('#searchBox').classList.remove('hidden');
    $('#searchBox').innerHTML = r.results.length ? r.results.map((h) => `<p><span class="src">📄 ${esc(h.source.filename)} p.${h.source.page}</span> <span class="faint">score ${h.score}</span><br>${esc(h.text.slice(0, 300))}</p>`).join('')
      : '<p class="faint">Not found in this curriculum.</p>';
  };
  const list = (arr, f) => (arr && arr.length ? `<ul>${arr.map((x) => `<li>${f(x)}</li>`).join('')}</ul>` : '<span class="faint">none found</span>');
  async function openNode(id) {
    nodeId = id; const n = await api('/api/v1/nodes/' + id); $('#nodeTitle').textContent = n.title;
    const plan = n.plans[0];
    $('#nodeBox').innerHTML = `
      <details open><summary>Extracted from the document</summary>
        ${[['Objectives', list(n.objectives, esc)], ['Definitions', list(n.definitions, (d) => `<b>${esc(d.term)}</b>: ${esc(d.meaning)} <span class="src">p.${d.page}</span>`)],
          ['Examples', list(n.examples, (x) => esc(x.text))], ['Activities', list(n.activities, (x) => esc(x.text))],
          ['Questions', list(n.questions, (q) => `${esc(q.question)}${q.answer ? ` — <i>${esc(q.answer)}</i>` : ''}`)]]
          .map(([h, v]) => `<div style="margin-top:8px"><div style="font-weight:700">${h}</div>${v}</div>`).join('')}</details>
      <div class="row"><select id="persona" style="width:170px"><option value="">Persona: auto</option><option>friendly</option><option>storyteller</option><option>math</option><option>science</option><option>language</option><option>coding</option></select>
        <button class="primary" id="genPlan">Generate lesson plan</button></div>
      ${plan ? planEditor(plan) : '<span class="faint">No lesson plan yet.</span>'}
      <div class="row"><b>Materials:</b>${['flashcards', 'worksheet', 'quiz', 'illustration', 'video', 'story_video'].map((k) => `<button class="sm" data-mat="${k}">${k.replace('_', ' ')}</button>`).join('')}</div>
      <div id="mats">${n.materials.map(matHtml).join('')}</div>`;
    $('#genPlan').onclick = async () => { try { await api(`/api/v1/nodes/${id}/plans${$('#persona').value ? '?persona=' + $('#persona').value : ''}`, { method: 'POST' }); openNode(id); } catch (e) { toast(e.message); } };
    $('#nodeBox').querySelectorAll('[data-mat]').forEach((b) => { b.onclick = async () => { try { await api(`/api/v1/nodes/${id}/materials?kind=${b.dataset.mat}`, { method: 'POST' }); toast(b.dataset.mat + ' requested'); openNode(id); } catch (e) { toast(e.message); } }; });
    if (plan) bindPlan(plan);
    if (n.materials.some((m) => m.status === 'pending' || m.status === 'processing')) setTimeout(() => nodeId === id && openNode(id), 4000);
  }
  function matHtml(m) {
    const c = m.content || {};
    let body = '';
    if (m.kind === 'flashcards') body = list(c.cards, (x) => `<b>${esc(x.front)}</b> — ${esc(x.back)}`);
    else if (m.kind === 'quiz') body = list(c.items, (x) => `${esc(x.question)}${x.options ? ` <span class="faint">[${x.options.map(esc).join(' | ')}]</span>` : ''} — <i>${esc(x.answer)}</i>`);
    else if (m.kind === 'worksheet') body = (c.sections || []).map((s) => `<b>${esc(s.heading)}</b>${list(s.items, esc)}`).join('');
    else if (m.kind === 'video') body = c.studio_url ? `<a href="${esc(c.studio_url)}" target="_blank">Open in video studio</a>` : '';
    else if (m.kind === 'illustration' && m.has_file) body = `<img data-src="/api/v1/materials/${m.id}/file" alt="illustration" style="max-width:100%">`;
    setTimeout(() => document.querySelectorAll('img[data-src]').forEach(async (img) => { const b = await api(img.dataset.src).catch(() => null); if (b) img.src = URL.createObjectURL(b); img.removeAttribute('data-src'); }), 0);
    return `<details><summary>${esc(m.kind)} <span class="pill ${m.status === 'ready' ? 'good' : m.status === 'failed' ? 'bad' : 'warn'}">${m.status}</span>${m.error ? ' ' + esc(m.error) : ''}</summary>${body}</details>`;
  }
  function planEditor(p) {
    const c = p.content;
    return `<div class="card"><div class="head">Lesson plan <span class="pill ${p.status === 'approved' ? 'good' : 'warn'}">${p.status}</span><span class="faint">${esc(p.generator)} · ${esc(p.persona)}</span></div><div class="body stack">
      ${(c.gaps || []).length ? `<div class="pill warn">Not covered by the document: ${esc(c.gaps.join(', '))}</div>` : ''}
      <label class="f">Introduction<textarea id="pIntro" rows="2">${esc(c.introduction)}</textarea></label>
      <label class="f">Explanation<textarea id="pExpl" rows="4">${esc(c.explanation)}</textarea></label>
      <label class="f">Story<textarea id="pStory" rows="2">${esc(c.story)}</textarea></label>
      <label class="f">Questions (one per line: question | expected answer)<textarea id="pQs" rows="5">${esc((c.questions || []).map((q) => `${q.question} | ${q.expected_answer || ''}`).join('\n'))}</textarea></label>
      <label class="f">Activities (one per line)<textarea id="pActs" rows="2">${esc((c.activities || []).join('\n'))}</textarea></label>
      <div class="row"><button id="pSave">Save edits</button><button class="primary" id="pApprove">${p.status === 'approved' ? 'Approved ✓ (save & keep)' : 'Approve for teaching'}</button>
      <span class="faint">Sources: ${(c.sources || []).slice(0, 4).map((s) => `${esc(s.filename)} p.${s.page}`).join(', ')}</span></div></div></div>`;
  }
  function bindPlan(p) {
    const collect = () => ({ ...p.content, introduction: $('#pIntro').value, explanation: $('#pExpl').value, story: $('#pStory').value,
      activities: $('#pActs').value.split('\n').map((s) => s.trim()).filter(Boolean),
      questions: $('#pQs').value.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => { const [q, a] = l.split('|'); return { question: q.trim(), expected_answer: (a || '').trim() }; }) });
    $('#pSave').onclick = async () => { await api('/api/v1/plans/' + p.id, { method: 'PATCH', body: { content: collect() } }); toast('Saved'); };
    $('#pApprove').onclick = async () => { await api('/api/v1/plans/' + p.id, { method: 'PATCH', body: { content: collect(), status: 'approved' } }); toast('Approved — Mab Cat will teach from this plan'); openNode(nodeId); };
  }
  $('#classBtn').onclick = async () => { if (!$('#className').value) return; await api('/api/v1/classes?name=' + encodeURIComponent($('#className').value), { method: 'POST' }); loadClasses(); };
  $('#assignBtn').onclick = async () => { try { await api(`/api/v1/assignments?student_id=${$('#assignStu').value}&curriculum_id=${$('#assignCur').value}`, { method: 'POST' }); toast('Assigned'); } catch (e) { toast(e.message); } };
  async function loadClasses() {
    const cls = await api('/api/v1/classes').catch(() => []);
    $('#classes').innerHTML = cls.map((c) => `<div class="card"><div class="head">${esc(c.name)} <span class="pill brand">code ${esc(c.join_code)}</span><span class="spacer"></span><button class="sm" data-an="${c.id}">Analytics</button></div>
      <div class="body">${c.students.map((s) => esc(s.display_name)).join(', ') || '<span class="faint">Parents join with the class code.</span>'}<div id="an-${c.id}"></div></div></div>`).join('');
    $('#classes').querySelectorAll('[data-an]').forEach((b) => { b.onclick = async () => {
      const a = await api(`/api/v1/classes/${b.dataset.an}/analytics`);
      $('#an-' + b.dataset.an).innerHTML = `<p>Average accuracy: <b>${a.average_accuracy ?? '—'}</b></p><table class="t"><thead><tr><th>Student</th><th>Accuracy</th><th>Completed</th><th>Needs practice</th><th>Minutes</th></tr></thead>
        <tbody>${a.students.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.accuracy ?? '—'}</td><td>${s.completed_lessons}</td><td>${esc(s.needs_practice.join(', '))}</td><td>${s.learning_minutes}</td></tr>`).join('')}</tbody></table>`; }; });
  }

  /* ------------------------------ parent ------------------------------ */
  async function loadParent() {
    const [stus, devs] = await Promise.all([api('/api/v1/students'), api('/api/v1/devices')]);
    const opts = stus.map((s) => `<option value="${s.id}">${esc(s.display_name)}</option>`).join('');
    $('#dStu').innerHTML = '<option value="">(no child)</option>' + opts; $('#jStu').innerHTML = opts;
    $('#stuList').innerHTML = stus.map((s) => `<tr><td><a href="#" data-prog="${s.id}"><b>${esc(s.display_name)}</b></a><div class="faint">grade ${esc(s.grade || '?')} · ${esc(s.preferred_language)} · ${s.level}</div></td>
      <td><label class="row"><input type="checkbox" data-cl="${s.id}" ${s.consent_learning ? 'checked' : ''} style="width:auto"> learning</label><label class="row"><input type="checkbox" data-ca="${s.id}" ${s.consent_audio ? 'checked' : ''} style="width:auto"> voice</label></td>
      <td>${s.daily_limit_minutes || '∞'} min</td><td><button class="sm danger" data-sdel="${s.id}">Delete</button></td></tr>`).join('');
    $('#stuList').querySelectorAll('input[type=checkbox]').forEach((c) => { c.onchange = async () => {
      const id = c.dataset.cl || c.dataset.ca;
      await api(`/api/v1/students/${id}/consent`, { method: 'PUT', body: { learning: document.querySelector(`[data-cl="${id}"]`).checked, audio: document.querySelector(`[data-ca="${id}"]`).checked } });
      toast('Consent updated'); loadParent(); }; });
    $('#stuList').querySelectorAll('[data-sdel]').forEach((b) => { b.onclick = async () => { if (confirm('Delete this child and ALL their learning data?')) { await api('/api/v1/students/' + b.dataset.sdel, { method: 'DELETE' }); loadParent(); } }; });
    $('#stuList').querySelectorAll('[data-prog]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); progress(a.dataset.prog); }; });
    $('#devList').innerHTML = devs.map((d) => `<tr><td><b>${esc(d.id)}</b><div class="faint">${esc(d.name)} ${d.firmware ? '· fw ' + esc(d.firmware) : ''}</div></td>
      <td>${esc((stus.find((s) => s.id === d.student_id) || {}).display_name || '—')}</td><td class="faint">${d.last_seen ? new Date(d.last_seen + 'Z').toLocaleString() : 'never'}</td>
      <td><button class="sm" data-dtog="${d.id}" data-en="${d.enabled}">${d.enabled ? 'Disable' : 'Enable'}</button><button class="sm" data-rot="${d.id}">New token</button><button class="sm danger" data-ddel="${d.id}">Remove</button></td></tr>`).join('');
    $('#devList').querySelectorAll('[data-dtog]').forEach((b) => { b.onclick = async () => { await api(`/api/v1/devices/${b.dataset.dtog}?enabled=${b.dataset.en !== 'true'}`, { method: 'PATCH' }); loadParent(); }; });
    $('#devList').querySelectorAll('[data-rot]').forEach((b) => { b.onclick = async () => { const r = await api(`/api/v1/devices/${b.dataset.rot}/rotate-token`, { method: 'POST' }); showToken(b.dataset.rot, r.device_token); }; });
    $('#devList').querySelectorAll('[data-ddel]').forEach((b) => { b.onclick = async () => { if (confirm('Remove this device?')) { await api('/api/v1/devices/' + b.dataset.ddel, { method: 'DELETE' }); loadParent(); } }; });
  }
  function showToken(id, token) {
    $('#dToken').classList.remove('hidden');
    $('#dToken').innerHTML = `<div class="card"><div class="body"><b>Device token for ${esc(id)}</b> — shown once. Put it in the robot's configuration (secure storage), never in source code.<br><span class="mono">${esc(token)}</span></div></div>`;
  }
  $('#sBtn').onclick = async () => {
    if (!$('#sName').value) return toast('Enter a name');
    try { await api('/api/v1/students', { body: { display_name: $('#sName').value, grade: $('#sGrade').value, age: +$('#sAge').value || null, preferred_language: $('#sLang').value, daily_limit_minutes: +$('#sLimit').value } }); $('#sName').value = ''; loadParent(); } catch (e) { toast(e.message); }
  };
  $('#dBtn').onclick = async () => {
    try { const d = await api('/api/v1/devices', { body: { device_id: $('#dId').value.trim(), student_id: $('#dStu').value || null } }); showToken(d.id, d.device_token); loadParent(); } catch (e) { toast(e.message); }
  };
  $('#jBtn').onclick = async () => { try { const r = await api(`/api/v1/classes/join?join_code=${encodeURIComponent($('#jCode').value)}&student_id=${$('#jStu').value}`, { method: 'POST' }); toast('Joined ' + r.class); } catch (e) { toast(e.message); } };
  async function progress(id) {
    const p = await api(`/api/v1/students/${id}/progress`);
    $('#progress').innerHTML = `<dl class="kv"><dt>Level</dt><dd>${p.level}</dd><dt>Accuracy</dt><dd>${p.accuracy ?? '—'} (${p.correct}/${p.answers})</dd>
      <dt>Completed lessons</dt><dd>${p.completed_lessons}</dd><dt>Learning time</dt><dd>${p.learning_minutes} min (today ${p.minutes_today})</dd>
      <dt>Strengths</dt><dd>${esc(p.strengths.join(', ') || '—')}</dd><dt>Needs practice</dt><dd>${esc(p.needs_practice.join(', ') || '—')}</dd><dt>Next lesson</dt><dd>${esc(p.next ? p.next.title : '—')}</dd></dl>
      <table class="t"><thead><tr><th>Topic</th><th>Mastery</th><th>Attempts</th></tr></thead><tbody>${p.topics.map((t) => `<tr><td>${esc(t.title)}</td><td>${Math.round(t.mastery * 100)}%${t.completed ? ' ✓' : ''}</td><td>${t.attempts}</td></tr>`).join('')}</tbody></table>`;
  }

  /* ------------------------------ admin ------------------------------ */
  async function loadAdmin() {
    const [users, health, ov, audit] = await Promise.all([api('/api/v1/users'), api('/api/v1/health'), api('/api/v1/analytics/overview'), api('/api/v1/audit?limit=200')]);
    $('#users').innerHTML = users.map((u) => `<tr><td>${esc(u.email)}</td><td>${esc(u.name)}</td><td><span class="pill">${u.role}</span></td></tr>`).join('');
    const comp = (o) => Object.entries(o).map(([k, v]) => `<span class="pill ${v.available ? 'good' : 'bad'}" title="${esc(v.detail)}">${esc(k)}</span>`).join(' ');
    $('#health').innerHTML = `<dt>Database</dt><dd>${health.database}</dd><dt>LLM</dt><dd><span class="pill ${health.llm.available ? 'good' : 'bad'}">${health.llm.provider}</span> ${esc(health.llm.detail)}</dd>
      <dt>STT</dt><dd>${comp(health.stt)}</dd><dt>TTS</dt><dd>${comp(health.tts)}</dd><dt>Devices online</dt><dd>${esc(health.connected_devices.join(', ') || 'none')}</dd>`;
    $('#overview').innerHTML = Object.entries(ov).map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${v}</dd>`).join('');
    $('#audit').textContent = audit.map((a) => `${a.at}  ${a.actor}  ${a.action}  ${a.target}  ${JSON.stringify(a.detail)}`).join('\n');
  }
  $('#uBtn').onclick = async () => { try { await api('/api/v1/users', { body: { email: $('#uEmail').value, name: $('#uName').value, role: $('#uRole').value, password: $('#uPw').value } }); toast('Account created'); loadAdmin(); } catch (e) { toast(e.message); } };
})();
