// ngrok's free tier serves an HTML "You are about to visit..." interstitial
// page on the first request from a given browser instead of proxying
// through to the actual server — confirmed live: every dashboard fetch to
// a fresh ngrok tunnel URL failed with "Failed to fetch" because the
// browser got that HTML page back instead of real JSON. This header tells
// ngrok to skip it. Harmless to send even against a non-ngrok bot host
// (e.g. a VPS or Cloudflare Tunnel) — it's just an ignored extra header
// there.
export const BOT_API_HEADERS = { "ngrok-skip-browser-warning": "true" } as const;
