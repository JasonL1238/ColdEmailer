/**
 * App config from environment. Keys are set in .env (Vite: only VITE_* are exposed).
 * - VITE_API_BASE_URL: API base (default /api for dev proxy). Use full URL when backend is on another host.
 */
export const API_BASE_URL = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) || '/api'
