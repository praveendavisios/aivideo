/* Simulated RoboCat screen (800×480, like the Waveshare 4.3" LCD): eyes, ears, mouth, states. */
const Robot = (() => {
  const W = 800, H = 480;
  const svg = `
  <svg id="robot" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Mab Cat face">
    <defs><radialGradient id="bgG" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="#2b2366"/><stop offset="1" stop-color="#0b0e1d"/></radialGradient></defs>
    <rect width="${W}" height="${H}" fill="url(#bgG)"/>
    <g id="earL" style="transform-origin:250px 150px"><path d="M190 165 L235 40 L310 140 Z" fill="#8b74ff"/><path d="M215 150 L238 85 L280 140 Z" fill="#f6b6d8"/></g>
    <g id="earR" style="transform-origin:550px 150px"><path d="M610 165 L565 40 L490 140 Z" fill="#8b74ff"/><path d="M585 150 L562 85 L520 140 Z" fill="#f6b6d8"/></g>
    <rect x="160" y="120" width="480" height="320" rx="150" fill="#6c4df6"/>
    <g id="eyes"></g>
    <ellipse cx="400" cy="320" rx="16" ry="10" fill="#f6b6d8"/>
    <g id="mouth"></g>
    <g id="whiskers" stroke="#d9d0ff" stroke-width="5" stroke-linecap="round">
      <line x1="215" y1="320" x2="300" y2="330"/><line x1="215" y1="350" x2="300" y2="345"/>
      <line x1="585" y1="320" x2="500" y2="330"/><line x1="585" y1="350" x2="500" y2="345"/></g>
    <g id="extra"></g>
    <text id="state" x="400" y="468" text-anchor="middle" fill="#a99dff" font-family="Inter,sans-serif" font-size="20" font-weight="700"></text>
  </svg>`;
  const EYES = {
    neutral: () => eyePair((x) => `<ellipse cx="${x}" cy="250" rx="42" ry="52" fill="#fff"/><circle cx="${x}" cy="258" r="24" fill="#151831"/><circle cx="${x + 9}" cy="246" r="8" fill="#fff"/>`),
    happy: () => eyePair((x) => `<path d="M${x - 45} 262 Q${x} 205 ${x + 45} 262" stroke="#fff" stroke-width="14" fill="none" stroke-linecap="round"/>`),
    excited: () => eyePair((x) => star(x, 250, 46, 20)),
    thinking: () => eyePair((x, i) => `<ellipse cx="${x}" cy="250" rx="42" ry="${i ? 30 : 52}" fill="#fff"/><circle cx="${x + 14}" cy="${i ? 240 : 232}" r="22" fill="#151831"/>`),
    listening: () => eyePair((x) => `<ellipse cx="${x}" cy="250" rx="46" ry="58" fill="#fff"/><circle cx="${x}" cy="252" r="30" fill="#151831"/><circle cx="${x + 11}" cy="238" r="10" fill="#fff"/>`),
    speaking: () => EYES.neutral(),
    sleeping: () => eyePair((x) => `<path d="M${x - 42} 255 Q${x} 280 ${x + 42} 255" stroke="#fff" stroke-width="12" fill="none" stroke-linecap="round"/>`),
    concerned: () => eyePair((x, i) => `<ellipse cx="${x}" cy="258" rx="38" ry="44" fill="#fff"/><circle cx="${x}" cy="266" r="20" fill="#151831"/><line x1="${x - 40}" y1="${i ? 196 : 208}" x2="${x + 40}" y2="${i ? 208 : 196}" stroke="#fff" stroke-width="9" stroke-linecap="round"/>`),
    surprised: () => eyePair((x) => `<circle cx="${x}" cy="250" r="50" fill="#fff"/><circle cx="${x}" cy="250" r="18" fill="#151831"/>`),
  };
  function eyePair(f) { return f(300, 0) + f(500, 1); }
  function star(cx, cy, R, r) {
    let p = ''; for (let i = 0; i < 10; i++) { const a = Math.PI / 5 * i - Math.PI / 2; const rr = i % 2 ? r : R; p += `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)} `; }
    return `<polygon points="${p}" fill="#ffd84d"/>`;
  }
  const MOUTH = {
    closed: '<path d="M370 345 Q385 362 400 345 Q415 362 430 345" stroke="#151831" stroke-width="6" fill="none" stroke-linecap="round"/>',
    open: '<ellipse cx="400" cy="360" rx="26" ry="20" fill="#151831"/><ellipse cx="400" cy="370" rx="14" ry="8" fill="#f27aa6"/>',
    smile: '<path d="M355 345 Q400 395 445 345" stroke="#151831" stroke-width="7" fill="none" stroke-linecap="round"/>',
    flat: '<line x1="375" y1="355" x2="425" y2="355" stroke="#151831" stroke-width="6" stroke-linecap="round"/>',
  };
  let el, mouthTimer, state = 'neutral', ears = [50, 50];

  function mount(container) { container.innerHTML = svg; el = container.querySelector('svg'); set('neutral', 50, 50); }
  function earAngle(deg, side) { return `rotate(${(90 - deg) * 0.45 * side}deg)`; }  // 90 = upright, 0 = flat back

  function set(expression, earL, earR) {
    if (!el) return;
    state = EYES[expression] ? expression : 'neutral';
    el.querySelector('#eyes').innerHTML = EYES[state]();
    if (earL != null) ears[0] = earL; if (earR != null) ears[1] = earR;
    el.querySelector('#earL').style.transform = earAngle(ears[0], -1);
    el.querySelector('#earR').style.transform = earAngle(ears[1], 1);
    el.querySelector('#earL').style.transition = el.querySelector('#earR').style.transition = 'transform .35s';
    el.querySelector('#state').textContent = state.toUpperCase();
    const ex = el.querySelector('#extra');
    ex.innerHTML = state === 'thinking' ? '<circle cx="640" cy="150" r="12" fill="#a99dff"/><circle cx="670" cy="110" r="18" fill="#a99dff"/><circle cx="710" cy="60" r="26" fill="#a99dff"/>'
      : state === 'sleeping' ? '<text x="620" y="130" fill="#a99dff" font-size="54" font-weight="800">z</text><text x="665" y="85" fill="#a99dff" font-size="38" font-weight="800">z</text>'
      : state === 'listening' ? '<path d="M680 220 q30 30 0 60 M705 200 q50 50 0 100" stroke="#22b8e6" stroke-width="7" fill="none" stroke-linecap="round"/>' : '';
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
