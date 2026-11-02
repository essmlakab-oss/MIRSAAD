// Relative /api URLs work in production, while the Vite proxy handles local development.
// VITE_API_BASE_URL is the current name; the old VITE_TELEMETRY_API_BASE_URL is
// still honored so an existing frontend/.env keeps working without a rename.
const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ??
  import.meta.env.VITE_TELEMETRY_API_BASE_URL ??
  ""
).replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
