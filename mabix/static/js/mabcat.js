/* RoboCat API client for studio pages (bearer token from the unified login). */
window.mc = (() => {
  const token = () => document.querySelector('meta[name="mc-token"]')?.content || '';
  async function api(path, opts = {}) {
    const headers = { Accept: 'application/json', ...(opts.headers || {}) };
    if (token()) headers.Authorization = 'Bearer ' + token();
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    const r = await fetch('/api/v1' + path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
    if (r.status === 401) { ui.toast('Your session expired — please sign in again', 'bad'); setTimeout(() => { location.href = '/login'; }, 1200); throw new Error('Session expired'); }
    const ct = r.headers.get('content-type') || '';
    const data = ct.includes('json') ? await r.json() : await r.blob();
    if (!r.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join('; ') : `Request failed (${r.status})`);
    }
    return data;
  }
  const role = () => document.body.dataset.role;
  async function guard(fn, btn, label) {
    if (btn) ui.busy(btn, true, label);
    try { return await fn(); } catch (e) { ui.toast(e.message, 'bad'); return undefined; } finally { if (btn) ui.busy(btn, false); }
  }
  let centersCache = null;
  async function centers() { if (!centersCache) centersCache = await api('/centers').catch(() => []); return centersCache; }
  const centerName = (list, id) => (list.find((c) => c.id === id) || {}).name || '—';
  const opts = (items, value, empty) => (empty !== undefined ? `<option value="">${ui.esc(empty)}</option>` : '') +
    items.map((i) => `<option value="${ui.esc(i.id)}" ${i.id === value ? 'selected' : ''}>${ui.esc(i.label)}</option>`).join('');
  const pill = (ok, yes, no) => `<span class="badge ${ok ? 'good' : ''}"><span class="dot"></span>${ok ? yes : no}</span>`;
  return { api, role, guard, centers, centerName, opts, pill, resetCenters: () => { centersCache = null; } };
})();
