/* Shared console helpers: auth token, REST, WebSocket wire format, mic capture, PCM playback. */
if (window.self !== window.top) document.documentElement.classList.add('embedded');  // inside the Mabix app
const MC = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = { get: (k) => { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) { /* blocked */ } } };
  const token = () => store.get('mabcat-token');

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token()) headers.Authorization = 'Bearer ' + token();
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    const r = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
    if (r.status === 401 && path !== '/api/v1/auth/login') { store.set('mabcat-token', null); }
    const ct = r.headers.get('content-type') || '';
    const data = ct.includes('json') ? await r.json() : await r.blob();
    if (!r.ok) throw new Error((data && data.detail && (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))) || `HTTP ${r.status}`);
    return data;
  }

  async function login(email, password) {
    const d = await api('/api/v1/auth/login', { body: { email, password } });
    store.set('mabcat-token', d.token); store.set('mabcat-user', JSON.stringify(d.user));
    return d.user;
  }
  const user = () => { try { return JSON.parse(store.get('mabcat-user') || 'null'); } catch (e) { return null; } };
  function logout() { api('/api/v1/auth/logout', { method: 'POST' }).catch(() => {}); store.set('mabcat-token', null); store.set('mabcat-user', null); }

  function toast(msg) {
    const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), 3800);
  }

  /* ---- binary frames: "MC" | ver | kind | stream_id u32 | seq u32 | PCM ---- */
  function packFrame(kind, streamId, seq, pcmBytes) {
    const buf = new ArrayBuffer(12 + pcmBytes.byteLength); const v = new DataView(buf);
    v.setUint8(0, 77); v.setUint8(1, 67); v.setUint8(2, 1); v.setUint8(3, kind);
    v.setUint32(4, streamId, true); v.setUint32(8, seq, true);
    new Uint8Array(buf, 12).set(new Uint8Array(pcmBytes)); return buf;
  }
  function unpackFrame(buf) {
    const v = new DataView(buf);
    if (v.getUint8(0) !== 77 || v.getUint8(1) !== 67) return null;
    return { kind: v.getUint8(3), streamId: v.getUint32(4, true), seq: v.getUint32(8, true), pcm: buf.slice(12) };
  }

  function wsUrl(path) { return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path; }

  /* ---- microphone: AudioWorklet → Int16 PCM at the context's native rate ---- */
  const WORKLET = `class Cap extends AudioWorkletProcessor{constructor(){super();this.buf=[];this.n=0}
    process(i){const c=i[0]&&i[0][0];if(c){this.buf.push(new Float32Array(c));this.n+=c.length;
    if(this.n>=sampleRate/10){const o=new Float32Array(this.n);let p=0;for(const b of this.buf){o.set(b,p);p+=b.length}
    this.port.postMessage(o,[o.buffer]);this.buf=[];this.n=0}}return true}}registerProcessor('cap',Cap);`;

  async function startMic(onChunk, onLevel) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true } });
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' })));
    const src = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, 'cap');
    const recorded = [];
    node.port.onmessage = (e) => {
      const f = e.data; recorded.push(f);
      let s = 0; for (let i = 0; i < f.length; i++) s += f[i] * f[i];
      onLevel && onLevel(Math.sqrt(s / f.length));
      const pcm = new Int16Array(f.length);
      for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 32767;
      onChunk(pcm.buffer);
    };
    src.connect(node);
    return { sampleRate: ctx.sampleRate, recorded,
      stop() { stream.getTracks().forEach((t) => t.stop()); node.disconnect(); ctx.close(); } };
  }

  function wavBlob(chunks, sampleRate) {
    let n = 0; chunks.forEach((c) => { n += c.length; });
    const buf = new ArrayBuffer(44 + n * 2); const v = new DataView(buf);
    const w = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
    let o = 44; chunks.forEach((c) => { for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 32767, true); });
    return new Blob([buf], { type: 'audio/wav' });
  }

  /* ---- streamed TTS playback (PCM s16le) with interruption ---- */
  class Player {
    constructor() { this.ctx = null; this.sources = []; this.t = 0; this.rate = 16000; this.onstate = null; }
    start(rate) { this.rate = rate; if (!this.ctx) this.ctx = new AudioContext(); this.t = Math.max(this.ctx.currentTime + 0.05, this.t); }
    push(pcmBuf) {
      if (!this.ctx) this.start(this.rate);
      const i16 = new Int16Array(pcmBuf); const f = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f[i] = i16[i] / 32768;
      const b = this.ctx.createBuffer(1, f.length, this.rate); b.copyToChannel(f, 0);
      const s = this.ctx.createBufferSource(); s.buffer = b; s.connect(this.ctx.destination);
      this.t = Math.max(this.t, this.ctx.currentTime + 0.02); s.start(this.t); this.t += b.duration;
      this.sources.push(s); s.onended = () => { this.sources = this.sources.filter((x) => x !== s); if (!this.sources.length && this.onstate) this.onstate(false); };
      this.onstate && this.onstate(true);
    }
    stop() { this.sources.forEach((s) => { try { s.stop(); } catch (e) { /* ended */ } }); this.sources = []; if (this.ctx) this.t = this.ctx.currentTime; this.onstate && this.onstate(false); }
    get playing() { return this.sources.length > 0; }
  }

  function download(name, content, type = 'application/json') {
    const a = document.createElement('a'); a.href = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  return { $, esc, api, login, logout, user, token, toast, packFrame, unpackFrame, wsUrl, startMic, wavBlob, Player, download, store };
})();
