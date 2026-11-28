/* Mab Cat robot simulator: the RoboCat body with a live screen (the 800×480 LCD), ears and LED lights. */
const Robot = (() => {
  // Screen content is drawn in LCD coordinates (800 × 480) inside a nested <svg> on the robot's face.
  const IRIS = '#3140c9', RING = '#bdf3ff', LINE = '#e8f6ff';
  const eyeBall = (x, y = 240, r = 70) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${RING}"/><circle cx="${x}" cy="${y + 6}" r="${r * .8}" fill="${IRIS}"/>`;
  const EYES = {
    neutral: () => pair((x) => eyeBall(x) + `<circle cx="${x + 22}" cy="214" r="18" fill="#fff"/>`),
    happy: () => pair((x) => `<path d="M${x - 70} 262 Q${x} 170 ${x + 70} 262" stroke="${RING}" stroke-width="22" fill="none" stroke-linecap="round"/>`),
    excited: () => pair((x) => star(x, 240, 72, 32)),
    thinking: () => pair((x, i) => `<ellipse cx="${x}" cy="240" rx="70" ry="${i ? 44 : 70}" fill="${RING}"/><circle cx="${x + 24}" cy="${i ? 232 : 214}" r="${i ? 30 : 46}" fill="${IRIS}"/>`),
    listening: () => pair((x) => eyeBall(x, 236, 80) + `<circle cx="${x + 26}" cy="206" r="20" fill="#fff"/>`),
    speaking: () => EYES.neutral(),
    sleeping: () => pair((x) => `<path d="M${x - 66} 248 Q${x} 300 ${x + 66} 248" stroke="${RING}" stroke-width="18" fill="none" stroke-linecap="round"/>`),
    concerned: () => pair((x, i) => eyeBall(x, 252, 62) + `<line x1="${x - 64}" y1="${i ? 150 : 168}" x2="${x + 64}" y2="${i ? 168 : 150}" stroke="${RING}" stroke-width="14" stroke-linecap="round"/>`),
    surprised: () => pair((x) => `<circle cx="${x}" cy="236" r="80" fill="${RING}"/><circle cx="${x}" cy="236" r="30" fill="${IRIS}"/>`),
  };
  const pair = (f) => f(250, 0) + f(550, 1);
  function star(cx, cy, R, r) {
    let p = ''; for (let i = 0; i < 10; i++) { const a = Math.PI / 5 * i - Math.PI / 2; const rr = i % 2 ? r : R; p += `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)} `; }
    return `<polygon points="${p}" fill="#ffd84d"/>`;
  }
  const MOUTH = {
    closed: `<path d="M350 372 Q375 400 400 372 Q425 400 450 372" stroke="${LINE}" stroke-width="9" fill="#f49bc4" stroke-linejoin="round"/>`,
    open: `<ellipse cx="400" cy="384" rx="38" ry="28" fill="#0b0d2b" stroke="${LINE}" stroke-width="6"/><ellipse cx="400" cy="396" rx="20" ry="10" fill="#f49bc4"/>`,
    smile: `<path d="M330 362 Q400 440 470 362" stroke="${LINE}" stroke-width="10" fill="#f49bc4" stroke-linejoin="round"/>`,
    flat: `<line x1="360" y1="384" x2="440" y2="384" stroke="${LINE}" stroke-width="9" stroke-linecap="round"/>`,
  };
  const LED = { neutral: '#22d3ee', listening: '#22d3ee', speaking: '#34d399', thinking: '#a78bfa', happy: '#22d3ee',
    excited: '#fbbf24', concerned: '#fb923c', surprised: '#f472b6', sleeping: '#334155' };
  const dots = [150, 166, 182, 198].map((y) => [56, 68].map((x) => `<circle cx="${x}" cy="${y}" r="3"/><circle cx="${400 - x}" cy="${y}" r="3"/>`).join('')).join('');
  const svg = `
  <svg id="robot" viewBox="0 0 400 430" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Mab Cat robot">
    <defs><linearGradient id="rbShell" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#e3e9f7"/></linearGradient>
    <linearGradient id="rbEar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c26bff"/><stop offset="1" stop-color="#3b5bff"/></linearGradient>
    <linearGradient id="rbBlue" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2a4fd6"/><stop offset="1" stop-color="#1c3aa8"/></linearGradient>
    <linearGradient id="rbTail" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#2a4fd6"/><stop offset="1" stop-color="#b56cff"/></linearGradient>
    <filter id="rbGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter></defs>
    <ellipse id="baseGlow" cx="200" cy="410" rx="150" ry="14" fill="#22d3ee" opacity=".6" filter="url(#rbGlow)"/>
    <g id="tail" style="transform-origin:318px 318px"><path d="M318 318 q40 -30 34 -80" stroke="url(#rbTail)" stroke-width="26" fill="none" stroke-linecap="round"/>
      <ellipse cx="352" cy="234" rx="13" ry="19" fill="#c9a6ff"/></g>
    <rect x="78" y="250" width="244" height="150" rx="60" fill="url(#rbBlue)"/><rect x="104" y="258" width="192" height="142" rx="52" fill="url(#rbShell)"/>
    <rect x="160" y="350" width="80" height="22" rx="11" fill="#141a45"/>
    <text x="200" y="366" text-anchor="middle" font-family="Inter,sans-serif" font-size="12" font-weight="800" fill="#5ce1ff">MabixAI</text>
    <path d="M96 400 a28 24 0 0 1 56 0 z M248 400 a28 24 0 0 1 56 0 z" fill="#e7defd"/>
    <rect id="baseLed" x="96" y="398" width="208" height="6" rx="3" fill="#22d3ee"/>
    <g id="earL" style="transform-origin:140px 110px"><path d="M78 120 L104 22 Q110 10 120 20 L176 76 Z" fill="url(#rbShell)"/><path d="M98 104 L112 44 L156 80 Z" fill="url(#rbEar)"/></g>
    <g id="earR" style="transform-origin:260px 110px"><path d="M322 120 L296 22 Q290 10 280 20 L224 76 Z" fill="url(#rbShell)"/><path d="M302 104 L288 44 L244 80 Z" fill="url(#rbEar)"/></g>
    <rect x="44" y="64" width="312" height="214" rx="86" fill="url(#rbShell)" stroke="#d8def0" stroke-width="2"/>
    <rect id="ledGlow" x="172" y="74" width="56" height="12" rx="6" fill="#22d3ee" filter="url(#rbGlow)"/>
    <rect id="led" x="176" y="76" width="48" height="9" rx="4.5" fill="#7ff0ff"/>
    <g fill="#3a3f5c"><circle cx="128" cy="84" r="3"/><circle cx="136" cy="90" r="3"/><circle cx="272" cy="84" r="3"/><circle cx="264" cy="90" r="3"/></g>
    <g fill="#c9d0e4">${dots}</g>
    <svg x="80" y="98" width="240" height="158" viewBox="0 0 800 480">
      <rect x="6" y="6" width="788" height="468" rx="110" fill="#1c1d72" stroke="#0b0d2b" stroke-width="12"/>
      <path d="M30 120 Q30 30 140 30 H640 Q740 30 760 90 Q420 50 30 220 Z" fill="#fff" opacity=".06"/>
      <g id="brows"></g><g id="eyes"></g>
      <path d="M380 318 l20 20 l20 -20 z" fill="#9fdcff"/>
      <g id="mouth"></g>
      <g stroke="#9fdcff" stroke-width="7" stroke-linecap="round" opacity=".8"><line x1="60" y1="330" x2="170" y2="340"/><line x1="60" y1="370" x2="165" y2="362"/>
        <line x1="740" y1="330" x2="630" y2="340"/><line x1="740" y1="370" x2="635" y2="362"/></g>
      <g id="extra"></g>
      <text id="state" x="400" y="452" text-anchor="middle" fill="#8fa2ff" font-family="Inter,sans-serif" font-size="30" font-weight="700" letter-spacing="4"></text>
    </svg>
  </svg>`;
  let el, mouthTimer, state = 'neutral', ears = [50, 50];

  function mount(container) { container.innerHTML = svg; el = container.querySelector('#robot'); set('neutral', 50, 50); }
  function earAngle(deg, side) { return `rotate(${(90 - deg) * 0.35 * side}deg)`; }  // 90 = upright, 0 = flat back

  function set(expression, earL, earR) {
    if (!el) return;
    state = EYES[expression] ? expression : 'neutral';
    el.querySelector('#eyes').innerHTML = EYES[state]();
    el.querySelector('#brows').innerHTML = ['neutral', 'listening', 'speaking', 'happy'].includes(state)
      ? `<path d="M170 120 q80 -44 160 0 M470 120 q80 -44 160 0" stroke="#7fe3ff" stroke-width="16" fill="none" stroke-linecap="round"/>` : '';
    if (earL != null) ears[0] = earL; if (earR != null) ears[1] = earR;
    for (const [id, v, side] of [['#earL', ears[0], -1], ['#earR', ears[1], 1]]) {
      const g = el.querySelector(id); g.style.transition = 'transform .35s ease'; g.style.transform = earAngle(v, side);
    }
    const c = LED[state] || LED.neutral;
    ['#ledGlow', '#baseGlow', '#baseLed'].forEach((id) => { el.querySelector(id).setAttribute('fill', c); });
    el.querySelector('#led').setAttribute('fill', state === 'sleeping' ? '#475569' : '#e6fdff');
    el.querySelector('#tail').style.animation = ['happy', 'excited'].includes(state) ? 'rbwag .6s ease-in-out 3' : '';
    el.querySelector('#state').textContent = state.toUpperCase();
    el.querySelector('#extra').innerHTML = state === 'thinking' ? '<circle cx="660" cy="130" r="16" fill="#a78bfa"/><circle cx="700" cy="88" r="24" fill="#a78bfa"/><circle cx="740" cy="40" r="10" fill="#a78bfa"/>'
      : state === 'sleeping' ? '<text x="640" y="150" fill="#8fa2ff" font-size="80" font-weight="800">z</text><text x="700" y="90" fill="#8fa2ff" font-size="56" font-weight="800">z</text>'
      : state === 'listening' ? '<path d="M690 200 q36 40 0 80 M720 176 q66 64 0 128" stroke="#22d3ee" stroke-width="10" fill="none" stroke-linecap="round"/>' : '';
    clearInterval(mouthTimer);
    const m = el.querySelector('#mouth');
    if (state === 'speaking') {
      let open = false; mouthTimer = setInterval(() => { open = !open; m.innerHTML = open ? MOUTH.open : MOUTH.closed; }, 160);
    } else m.innerHTML = { happy: MOUTH.smile, excited: MOUTH.smile, concerned: MOUTH.flat, sleeping: MOUTH.flat, surprised: MOUTH.open }[state] || MOUTH.closed;
  }
  return { mount, set, get state() { return state; } };
})();

/* Mab Cat character art for the conversation (avatar + welcome illustration). */
const MabCatArt = (() => {
  const face = (attrs = '') => `<svg viewBox="0 0 120 80" ${attrs} aria-hidden="true">
    <rect x="2" y="2" width="116" height="76" rx="18" fill="#1c1d72" stroke="#0b0d2b" stroke-width="3"/>
    <path d="M6 22 Q6 6 22 6 H98 Q110 6 113 16 Q70 10 6 40 Z" fill="#fff" opacity=".07"/>
    <path d="M26 24 q10 -6 20 0 M74 24 q10 -6 20 0" stroke="#7fe3ff" stroke-width="2.6" fill="none" stroke-linecap="round"/>
    <g class="mc-eyes"><circle cx="36" cy="40" r="13" fill="#bdf3ff"/><circle cx="36" cy="41" r="10.5" fill="#3140c9"/><circle cx="40" cy="36" r="3.6" fill="#fff"/>
    <circle cx="84" cy="40" r="13" fill="#bdf3ff"/><circle cx="84" cy="41" r="10.5" fill="#3140c9"/><circle cx="88" cy="36" r="3.6" fill="#fff"/></g>
    <path d="M56 50 l4 4 l4 -4 z" fill="#9fdcff"/><path d="M52 56 q4 6 8 0 q4 6 8 0" stroke="#e8f6ff" stroke-width="2.2" fill="#f49bc4" stroke-linejoin="round"/>
    <path d="M10 46 h16 M10 53 h15 M94 46 h16 M95 53 h15" stroke="#9fdcff" stroke-width="1.8" stroke-linecap="round" opacity=".8"/></svg>`;
  const avatar = () => `<span class="mc-avatar">${face('width="100%" height="100%"')}</span>`;
  const dots = [150, 166, 182, 198].map((y) => [56, 68].map((x) => `<circle cx="${x}" cy="${y}" r="3"/><circle cx="${400 - x}" cy="${y}" r="3"/>`).join('')).join('');
  const robot = () => `<svg class="mc-robot" viewBox="0 0 400 430" role="img" aria-label="Mab Cat">
    <defs><linearGradient id="mcShell" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#e3e9f7"/></linearGradient>
    <linearGradient id="mcEar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c26bff"/><stop offset="1" stop-color="#3b5bff"/></linearGradient>
    <linearGradient id="mcBlue" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2a4fd6"/><stop offset="1" stop-color="#1c3aa8"/></linearGradient>
    <linearGradient id="mcTail" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#2a4fd6"/><stop offset="1" stop-color="#b56cff"/></linearGradient>
    <filter id="mcGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter></defs>
    <ellipse cx="200" cy="408" rx="150" ry="14" fill="#22d3ee" opacity=".55" filter="url(#mcGlow)"/>
    <path d="M318 318 q40 -30 34 -80" stroke="url(#mcTail)" stroke-width="26" fill="none" stroke-linecap="round"/>
    <ellipse cx="352" cy="234" rx="13" ry="19" fill="#c9a6ff"/>
    <rect x="78" y="250" width="244" height="150" rx="60" fill="url(#mcBlue)"/><rect x="104" y="258" width="192" height="142" rx="52" fill="url(#mcShell)"/>
    <rect x="160" y="350" width="80" height="22" rx="11" fill="#141a45"/>
    <text x="200" y="366" text-anchor="middle" font-family="Inter,sans-serif" font-size="12" font-weight="800" fill="#5ce1ff">MabixAI</text>
    <path d="M96 400 a28 24 0 0 1 56 0 z M248 400 a28 24 0 0 1 56 0 z" fill="#e7defd"/><rect x="96" y="398" width="208" height="6" rx="3" fill="#22d3ee"/>
    <path d="M78 120 L104 22 Q110 10 120 20 L176 76 Z" fill="url(#mcShell)"/><path d="M98 104 L112 44 L156 80 Z" fill="url(#mcEar)"/>
    <path d="M322 120 L296 22 Q290 10 280 20 L224 76 Z" fill="url(#mcShell)"/><path d="M302 104 L288 44 L244 80 Z" fill="url(#mcEar)"/>
    <rect x="44" y="64" width="312" height="214" rx="86" fill="url(#mcShell)" stroke="#d8def0" stroke-width="2"/>
    <rect x="176" y="76" width="48" height="9" rx="4.5" fill="#7ff0ff"/>
    <g fill="#3a3f5c"><circle cx="128" cy="84" r="3"/><circle cx="136" cy="90" r="3"/><circle cx="272" cy="84" r="3"/><circle cx="264" cy="90" r="3"/></g>
    <g fill="#c9d0e4">${dots}</g>${face('x="86" y="100" width="228" height="152"')}</svg>`;
  return { face, avatar, robot };
})();
