/* Centralized API client: CSRF, JSON, errors, upload progress. */
(function () {
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data || {}; }
  }

  async function request(method, url, body) {
    const opts = { method, headers: { 'Accept': 'application/json' }, credentials: 'same-origin' };
    if (method !== 'GET') opts.headers['X-CSRF-Token'] = csrf();
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, opts); }
    catch (e) { throw new ApiError('Network error — is the Mabix server running?', 0); }
    if (res.status === 401) { window.location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new ApiError('Session expired', 401); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status, data);
    return data;
  }

  function upload(url, formData, onProgress) {
    let xhr;
    const promise = new Promise((resolve, reject) => {
      xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-CSRF-Token', csrf());
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError(data.error || `Upload failed (${xhr.status})`, xhr.status, data));
      };
      xhr.onerror = () => reject(new ApiError('Network error during upload', 0));
      xhr.onabort = () => reject(new ApiError('Upload cancelled', 0));
      xhr.send(formData);
    });
    promise.abort = () => xhr && xhr.abort();
    return promise;
  }

  window.api = {
    get: (u) => request('GET', u),
    post: (u, b) => request('POST', u, b === undefined ? {} : b),
    put: (u, b) => request('PUT', u, b),
    patch: (u, b) => request('PATCH', u, b),
    del: (u) => request('DELETE', u),
    upload,
    ApiError,
  };
})();
