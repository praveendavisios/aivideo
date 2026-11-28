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
