/**
 * App config from environment. Keys are set in .env (Vite: only VITE_* are exposed).
 * - VITE_TELEMETRY_URL: optional; when set, telemetry events are sent here. Leave unset in production.
 * - VITE_API_BASE_URL: API base (default /api for dev proxy). Use full URL when backend is on another host.
 */
export const TELEMETRY_URL = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_TELEMETRY_URL) || ''
export const API_BASE_URL = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) || '/api'

/** Send a telemetry event only if VITE_TELEMETRY_URL is set. Safe to call everywhere. */
export function sendTelemetry(location, message, data = {}) {
  if (!TELEMETRY_URL) return
  fetch(TELEMETRY_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ location, message, data, timestamp: Date.now(), runId: 'run1', hypothesisId: 'B' }),
  }).catch(() => {})
}
