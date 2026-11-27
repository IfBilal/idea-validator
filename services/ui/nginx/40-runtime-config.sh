#!/bin/sh
# Runs at container start (nginx-unprivileged runs /docker-entrypoint.d/*.sh as uid 101).
# Writes /tmp/config.js and /tmp/headers.conf from API_BASE_URL so the API address changes without
# rebuilding the image. API_BASE_URL is validated strictly: it ends up inside JavaScript and inside a
# Content-Security-Policy header, so anything unexpected stops the container instead of being escaped.
set -eu

OUT_DIR="${CONFIG_OUT_DIR:-/tmp}"
API_BASE_URL="${API_BASE_URL:-/api}"

case "$API_BASE_URL" in
  *[!A-Za-z0-9_./:-]*) echo "API_BASE_URL contains forbidden characters" >&2; exit 1 ;;
esac

ORIGIN=""
case "$API_BASE_URL" in
  //*) echo "API_BASE_URL must not be protocol-relative" >&2; exit 1 ;;
  /*) ;;                                  # same-origin path such as /api
  http://*|https://*)
    # origin = scheme://host[:port], used in the CSP connect-src
    ORIGIN=$(printf '%s' "$API_BASE_URL" | sed -E 's#^(https?://[^/]+).*#\1#')
    ;;
  *) echo "API_BASE_URL must be a path like /api or an http(s) URL" >&2; exit 1 ;;
esac

printf 'window.__APP_CONFIG__ = { API_BASE_URL: "%s" };\n' "$API_BASE_URL" > "$OUT_DIR/config.js"

CSP="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ${ORIGIN}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
{
  printf 'add_header Content-Security-Policy "%s" always;\n' "$CSP"
  printf 'add_header X-Content-Type-Options "nosniff" always;\n'
  printf 'add_header X-Frame-Options "DENY" always;\n'
  printf 'add_header Referrer-Policy "no-referrer" always;\n'
  printf 'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;\n'
} > "$OUT_DIR/headers.conf"
