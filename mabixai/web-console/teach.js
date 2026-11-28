(() => {
  const { $, esc, api } = MC;
  let ws = null, sessionId = null, player = new MC.Player(), mic = null, micSeq = 0, logLines = [], bins = 0;
  let currentQuiz = null;
  Robot.mount($('#robotBox'));
  player.onstate = (playing) => {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: playing ? 'playback_started' : 'playback_finished' }));
    if (!playing && Robot.state === 'speaking') Robot.set('listening');
  };

  /* ---------- log ---------- */
  function log(dir, msg) {
    const line = `${new Date().toLocaleTimeString()} ${dir} ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`;
    logLines.push(line);
    if (dir === '·bin' && !$('#showBin').checked) return;
    const d = document.createElement('div'); d.className = { '→': 'out', '←': 'in', '!': 'err', '·bin': 'bin' }[dir] || '';
    d.textContent = line; $('#log').appendChild(d); $('#log').scrollTop = 1e9;
  }
  $('#dlLog').onclick = () => MC.download('mabcat-log.txt', logLines.join('\n'), 'text/plain');
  $('#clrLog').onclick = () => { $('#log').innerHTML = ''; logLines = []; };

  function send(msg) { if (!ws || ws.readyState !== 1) return MC.toast('Not connected'); ws.send(JSON.stringify(msg)); log('→', msg); }

  /* ---------- chat ---------- */
  function bubble(cls, html, meta = '') {
    const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html + (meta ? `<div class="meta">${meta}</div>` : '');
    $('#chat').appendChild(d); $('#chat').scrollTop = 1e9; return d;
  }
  const srcChips = (s) => (s || []).map((x) => `<span class="src">📄 ${esc(x.filename || 'source')} p.${esc(x.page)}</span>`).join('');

  /* ---------- sign-in & setup ---------- */
  async function afterLogin() {
    $('#loginCard').classList.add('hidden'); $('#setupCard').classList.remove('hidden');
    const [devices, students] = await Promise.all([api('/api/v1/devices'), api('/api/v1/students')]);
    $('#device').innerHTML = devices.map((d) => `<option value="${esc(d.id)}">${esc(d.id)} — ${esc(d.name)}</option>`).join('') || '<option value="">(register a device in the portal)</option>';
    $('#student').innerHTML = students.map((s) => `<option value="${esc(s.id)}">${esc(s.display_name)} (grade ${esc(s.grade || '?')})${s.consent_learning ? '' : ' — no consent'}</option>`).join('');
    const linked = devices[0] && devices[0].student_id; if (linked) $('#student').value = linked;
    loadLessons();
  }
  $('#loginBtn').onclick = async () => { try { await MC.login($('#email').value, $('#pw').value); afterLogin(); } catch (e) { MC.toast(e.message); } };
  $('#pw').onkeydown = (e) => { if (e.key === 'Enter') $('#loginBtn').click(); };
  $('#logoutBtn').onclick = () => { MC.logout(); location.reload(); };
  if (MC.token()) afterLogin().catch(() => { MC.logout(); });

  let assignments = [];
  async function loadLessons() {
    const sid = $('#student').value; if (!sid) return;
    try { assignments = await api(`/api/v1/students/${sid}/assignments`); } catch (e) { assignments = []; }
    $('#curriculum').innerHTML = assignments.map((a, i) => `<option value="${i}">${esc(a.title)}</option>`).join('') || '<option>(nothing assigned — assign in the portal)</option>';
    fillLessons();
  }
  function fillLessons() {
    const a = assignments[+$('#curriculum').value];
    $('#lesson').innerHTML = a ? a.lessons.map((l) => `<option value="${esc(l.node_id)}">${esc(l.title)}</option>`).join('') : '';
  }
  $('#student').onchange = loadLessons; $('#curriculum').onchange = fillLessons;

  /* ---------- WebSocket ---------- */
  $('#connectBtn').onclick = () => {
    if (ws) ws.close();
    const device = $('#device').value; if (!device) return MC.toast('Register a device first');
    ws = new WebSocket(MC.wsUrl(`/ws/mabcat/${encodeURIComponent(device)}`)); ws.binaryType = 'arraybuffer';
    $('#conn').textContent = 'connecting…'; $('#conn').className = 'pill warn';
    ws.onopen = () => { ws.send(JSON.stringify({ type: 'auth', user_token: MC.token() })); log('→', { type: 'auth', user_token: '***' }); };
    ws.onclose = (e) => { $('#conn').textContent = `disconnected (${e.code})`; $('#conn').className = 'pill bad'; setEnabled(false); stopMic(); clearInterval(hb); };
    ws.onmessage = (e) => (typeof e.data === 'string' ? onJson(JSON.parse(e.data)) : onBinary(e.data));
  };
  let hb;
  function setEnabled(on) { ['#text', '#sendBtn', '#micBtn', '#lessonBtn', '#endBtn'].forEach((s) => { $(s).disabled = !on; }); }

  function onBinary(buf) {
    const f = MC.unpackFrame(buf); if (!f) return log('!', 'bad binary frame');
    bins++; if (bins % 10 === 1) log('·bin', `tts frame stream=${f.streamId} seq=${f.seq} ${f.pcm.byteLength}B`);
    if (f.kind === 2) player.push(f.pcm);
  }

  function onJson(m) {
    log('←', m);
    switch (m.type) {
      case 'connected':
        $('#conn').textContent = 'connected'; $('#conn').className = 'pill good';
        send({ type: 'session_start', student_id: $('#student').value, language: $('#lang').value === 'auto' ? undefined : $('#lang').value.split('-')[0],
          tts: $('#tts').checked, tts_sample_rate: +$('#ttsRate').value });
        hb = setInterval(() => send({ type: 'heartbeat' }), 25000); break;
      case 'session_started':
        sessionId = m.session_id; setEnabled(true); bubble('system', `Session ${esc(m.session_id)} with ${esc(m.student)}`);
        if (m.next_lesson) $('#nextLesson').textContent = `Recommended next: ${m.next_lesson.title}`; break;
      case 'teacher_response': {
        const meta = `<span>${esc(m.engine)}</span>${m.in_curriculum === false ? '<span>not in curriculum</span>' : ''}${srcChips(m.sources)}`;
        bubble('teacher', esc(m.text), meta); break; }
      case 'quiz_question': showQuiz(m); break;
      case 'answer_evaluation': bubble('system', `${m.correct ? '✅ correct' : '❌ not yet'} · score ${m.score} · ${esc(m.evaluator)} · attempt ${m.attempt}`); break;
      case 'lesson_update':
        $('#lessonPill').textContent = m.step === 'completed' ? `completed ${m.score}` : `lesson: ${m.title || ''}`;
        $('#lessonPill').className = 'pill ' + (m.step === 'completed' ? 'good' : 'brand');
        if (m.step === 'completed') { $('#quizBox').classList.add('hidden'); currentQuiz = null; if (m.next) $('#nextLesson').textContent = `Next: ${m.next.title}`; }
        if (m.plan_generator) bubble('system', `Lesson plan: ${esc(m.plan_generator)} · ${m.questions} questions`); break;
      case 'robot_expression': Robot.set(m.expression, m.ear_left, m.ear_right); $('#earL').value = m.ear_left ?? $('#earL').value; $('#earR').value = m.ear_right ?? $('#earR').value; break;
      case 'tts_audio_start': player.start(m.sample_rate); break;
      case 'tts_audio_end': if (m.interrupted) player.stop(); break;
      case 'stop_speaking': player.stop(); bubble('system', '✋ interrupted — Mab Cat stopped talking'); break;
      case 'student_transcript': bubble('student', esc(m.text), `<span>🎤 ${esc(m.provider)}</span><span>${esc(m.language)}</span>`); break;
      case 'partial_transcript': $('#partial').textContent = `… ${m.text}`; break;
      case 'final_transcript': $('#partial').textContent = `final (${m.provider}, ${m.language}, ${m.processing_ms} ms)`; break;
      case 'speech_detected': Robot.set('listening', 25, 25); break;
      case 'media_ready': $('#images').insertAdjacentHTML('afterbegin', `<p><img src="${esc(m.url)}?t=${Date.now()}" alt="Generated illustration"></p>`); authImgs(); break;
      case 'usage_limit_reached': bubble('system', '⏰ Daily learning time reached'); break;
      case 'session_ended': bubble('system', `Session ended · ${m.summary.correct}/${m.summary.answers} correct · ${m.summary.learning_minutes} min`); sessionId = null; setEnabled(false); break;
      case 'error': case 'transcription_error': bubble('system', `⚠ ${esc(m.code)}: ${esc(m.message)}`); break;
      default: break;
    }
  }

  async function authImgs() {  // images need the bearer token, so fetch them as blobs
    for (const img of document.querySelectorAll('#images img[src^="/api/"]')) {
      try { const b = await api(img.getAttribute('src').split('?')[0]); img.src = URL.createObjectURL(b); } catch (e) { /* not ready */ }
    }
  }

  function showQuiz(q) {
    currentQuiz = q;
    bubble('teacher quiz', `<b>Question ${q.question_index}/${q.total}</b><br>${esc(q.text)}`);
    const box = $('#quizBox'); box.classList.remove('hidden');
    box.innerHTML = q.options ? `<div class="row">${q.options.map((o, i) => `<button class="sm" data-opt="${i}">${esc(o)}</button>`).join('')}</div>`
      : '<span class="faint">Answer by typing or with 🎤 — answers are evaluated, not just accepted.</span>';
    box.querySelectorAll('[data-opt]').forEach((b) => { b.onclick = () => answer(q.options[+b.dataset.opt], +b.dataset.opt); });
  }
  function answer(text, optionIndex) {
    player.stop(); bubble('student', esc(text));
    const msg = { type: 'student_answer', text, input: optionIndex != null ? 'touch' : 'text' };
    if (optionIndex != null) { msg.option_index = optionIndex; msg.options = currentQuiz.options; }
    send(msg);
  }

  $('#sendBtn').onclick = () => {
    const t = $('#text').value.trim(); if (!t) return; $('#text').value = '';
    if (player.playing) { player.stop(); send({ type: 'interrupt' }); }
    if (currentQuiz) answer(t); else { bubble('student', esc(t)); send({ type: 'student_message', text: t, session_id: sessionId }); }
  };
  $('#text').onkeydown = (e) => { if (e.key === 'Enter') $('#sendBtn').click(); };
  $('#lessonBtn').onclick = () => { if ($('#lesson').value) send({ type: 'start_lesson', node_id: $('#lesson').value }); };
  $('#endBtn').onclick = () => send({ type: 'session_end' });

  /* ---------- microphone over the teaching socket ---------- */
  async function startMic() {
    try {
      mic = await MC.startMic((pcm) => { if (ws && ws.readyState === 1) ws.send(MC.packFrame(1, 1, micSeq++, pcm)); },
        (rms) => { $('#level').style.width = Math.min(100, rms * 400) + '%'; });
    } catch (e) { return MC.toast('Microphone unavailable: ' + e.message); }
    micSeq = 0;
    send({ type: 'audio_start', student_id: $('#student').value, sample_rate: mic.sampleRate, channels: 1, audio_format: 'pcm_s16le',
      language: $('#lang').value, stream_id: 1 });
    $('#micBtn').textContent = '⏹ Stop'; $('#micBtn').classList.add('rec'); $('#micState').textContent = `listening @ ${mic.sampleRate} Hz`;
    Robot.set('listening', 25, 25);
  }
  function stopMic() {
    if (!mic) return; mic.stop(); mic = null;
    if (ws && ws.readyState === 1) send({ type: 'audio_stop' });
    $('#micBtn').textContent = '🎤 Talk'; $('#micBtn').classList.remove('rec'); $('#micState').textContent = 'mic off'; $('#level').style.width = '0';
  }
  $('#micBtn').onclick = () => (mic ? stopMic() : startMic());

  /* ---------- robot commands ---------- */
  ['neutral', 'happy', 'excited', 'thinking', 'listening', 'speaking', 'sleeping', 'concerned', 'surprised'].forEach((x) => {
    const b = document.createElement('button'); b.className = 'sm'; b.textContent = x;
    b.onclick = () => { Robot.set(x); if (ws && ws.readyState === 1) send({ type: 'robot_expression', expression: x, ear_left: +$('#earL').value, ear_right: +$('#earR').value }); };
    $('#exprBtns').appendChild(b);
  });
  $('#sendEars').onclick = () => { Robot.set(Robot.state, +$('#earL').value, +$('#earR').value); send({ type: 'robot_expression', expression: Robot.state, ear_left: +$('#earL').value, ear_right: +$('#earR').value }); };
  ['#earL', '#earR'].forEach((s) => { $(s).oninput = () => Robot.set(Robot.state, +$('#earL').value, +$('#earR').value); });
})();
