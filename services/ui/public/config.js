// Runtime configuration. The container entrypoint rewrites this file from environment variables
// (API_BASE_URL), so the API address changes without a rebuild. Default /api = same origin behind the ingress.
window.__APP_CONFIG__ = { API_BASE_URL: "/api" };
