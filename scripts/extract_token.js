// Run in DevTools console on https://app.wisegolf.fi/#/golf/reservation/28 (logged in).
// Copies your bearer token + slug to clipboard. Paste into .env as WISEGOLF_TOKEN / WISEGOLF_HOST_SLUG.
(() => {
  const tokenKey = Object.keys(localStorage).find(k => k.startsWith('CapacitorStorage.access_token-'));
  const hostKey = Object.keys(localStorage).find(k => k.startsWith('CapacitorStorage.selectedHost-'));
  const token = tokenKey && localStorage.getItem(tokenKey);
  const slug = hostKey && JSON.parse(localStorage.getItem(hostKey));
  const out = `WISEGOLF_TOKEN=${token}\nWISEGOLF_HOST_SLUG=${slug}`;
  navigator.clipboard.writeText(out).then(() => console.log('copied:\n' + out)).catch(() => console.log(out));
  return out;
})();
