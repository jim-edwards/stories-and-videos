#!/bin/sh
# Starts a built image against an empty library and checks the basics: it comes up healthy, the
# sign-in page and security headers are served, nothing is visible without signing in, it is not
# running as root, and it also works under a different user id (as people set with `user:`).
#
#     scripts/smoke-test.sh IMAGE [docker|podman]
set -eu

image="${1:-}"
[ -n "$image" ] || { echo "Usage: $0 IMAGE [docker|podman]" >&2; exit 1; }
engine="${2:-}"
if [ -z "$engine" ]; then
  if command -v docker >/dev/null 2>&1; then engine=docker; else engine=podman; fi
fi
command -v curl >/dev/null 2>&1 || { echo "curl is needed" >&2; exit 1; }

work=$(mktemp -d)
name="sv-smoke-$$"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT INT TERM
fail() { echo "FAIL: $*" >&2; "$engine" logs "$name" 2>&1 | tail -n 30 >&2 || true; exit 1; }
ok() { echo "ok: $*"; }

# $1 = label, $2... = extra `run` options (for example --user)
check() {
  label="$1"; shift
  rm -rf "$work/videos" "$work/data"; mkdir "$work/videos" "$work/data"
  chmod 777 "$work/data"   # writable for whatever user id the container runs as
  "$engine" rm -f "$name" >/dev/null 2>&1 || true
  # The `z` option relabels the folders so SELinux hosts allow access; other hosts ignore it.
  "$engine" run -d --name "$name" -p 127.0.0.1::8080 "$@" \
    -v "$work/videos:/videos:ro,z" -v "$work/data:/data:z" "$image" >/dev/null || fail "$label: could not start"

  port=$("$engine" port "$name" 8080/tcp | head -n 1 | sed 's/.*://')
  [ -n "$port" ] || fail "$label: no published port"
  base="http://127.0.0.1:$port"

  tries=0
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "$base/healthz" || true)" = "200" ]; do
    tries=$((tries + 1)); [ "$tries" -le 60 ] || fail "$label: /healthz never became healthy"
    sleep 1
  done
  ok "$label: healthy"

  page=$(curl -s -D "$work/headers" "$base/login")
  case "$page" in *"Please sign in"*) ok "$label: sign-in page is served" ;; *) fail "$label: no sign-in page" ;; esac
  grep -qi '^content-security-policy:' "$work/headers" || fail "$label: missing Content-Security-Policy header"
  grep -qi '^x-content-type-options: *nosniff' "$work/headers" || fail "$label: missing nosniff header"
  ok "$label: security headers present"

  code=$(curl -s -o /dev/null -w '%{http_code}' "$base/")
  [ "$code" = "302" ] || [ "$code" = "303" ] || fail "$label: home page should redirect when signed out (got $code)"
  code=$(curl -s -o /dev/null -w '%{http_code}' "$base/media/1")
  [ "$code" != "200" ] || fail "$label: media is reachable without signing in"
  ok "$label: nothing is served without signing in"

  "$engine" logs "$name" 2>&1 | grep -q 'created the administrator account' || fail "$label: no first-start admin notice in the log"
  ok "$label: first start created the administrator"

  uid=$("$engine" exec "$name" id -u)
  [ "$uid" != "0" ] || fail "$label: running as root"
  ok "$label: running as uid $uid"
}

check "default user"
"$engine" exec "$name" ffprobe -version >/dev/null || fail "ffprobe is missing"
"$engine" exec "$name" sh -c 'command -v make-thumbnails >/dev/null' || fail "make-thumbnails is missing"
"$engine" exec "$name" test -s /usr/share/licenses/stories-and-videos/LICENSE || fail "the license text is missing from the image"
ok "ffprobe, make-thumbnails and the license are in the image"
"$engine" exec "$name" python -c "from zoneinfo import ZoneInfo; ZoneInfo('America/Chicago')" || fail "the time zone database is missing"
ok "time zone names work"

check "other user id" --user 12345:23456

echo "Smoke test passed for $image"
