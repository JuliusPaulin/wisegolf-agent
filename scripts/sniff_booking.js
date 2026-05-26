// Run BEFORE doing a real booking from web UI. Captures actual POST payloads.
// Paste into DevTools console on https://app.wisegolf.fi, then complete a booking manually.
// After: copy(JSON.stringify(window.__sniff)) and paste in recon/api-map.md
(() => {
  if (window.__sniff) return "already";
  window.__sniff = [];
  const oFetch = window.fetch;
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : input.url;
    const method = (init && init.method) || (typeof input !== 'string' && input.method) || 'GET';
    const body = init && init.body || null;
    const interesting = /reservations|carts|order|teetime|playerdetails/i.test(url) && method !== 'GET';
    const r = await oFetch.apply(this, arguments);
    if (interesting) {
      try {
        const respText = await r.clone().text();
        window.__sniff.push({ url: url.split('?')[0], method, reqBody: body, status: r.status, respLen: respText.length, respHead: respText.slice(0, 300) });
      } catch (e) {}
    }
    return r;
  };
  return "sniffer installed — now do a manual booking";
})();
