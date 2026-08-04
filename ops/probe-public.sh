#!/bin/sh
set -eu

origin=${1:-${CR8_PUBLIC_ORIGIN:-https://cr8.li}}
if [ -z "$origin" ]; then
  echo "usage: probe-public.sh https://public-origin" >&2
  exit 2
fi

# Cloudflare answers the default curl/Python user agent with a 403 before the
# request reaches the tunnel, so a probe of the real domain has to look like a
# browser or it measures Cloudflare's bot rules instead of this app.
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

probe_body=$(mktemp)
trap 'rm -f "$probe_body"' EXIT HUP INT TERM

check_login() {
  status=$(curl --silent --show-error \
    --output "$probe_body" --write-out '%{http_code}' \
    --user-agent "$UA" \
    --max-time 20 "${origin%/}/login")
  if [ "$status" != "200" ] || ! grep -qi "password" "$probe_body"; then
    echo "public login failed: /login returned $status" >&2
    exit 1
  fi
}

check_protected() {
  path=$1
  status=$(curl --silent --show-error \
    --output "$probe_body" --write-out '%{http_code}' \
    --user-agent "$UA" \
    --max-time 20 "${origin%/}${path}")
  case "$status" in
    303|401) ;;
    *)
      echo "authentication boundary failed: $path returned $status" >&2
      exit 1
      ;;
  esac
}

# The session cookie has to say Secure on a public origin, or a browser will
# happily send it back over plain http as well.
check_cookie_is_secure() {
  case "${origin}" in
    https://*) ;;
    *) return 0 ;;
  esac
  password_file="$(dirname "$0")/../secrets/owner-password.txt"
  [ -r "$password_file" ] || return 0
  # Read it rather than using curl's name@file form, which keeps the trailing
  # newline and quietly sends the wrong password.
  password=$(tr -d '\r\n' < "$password_file")
  cookie=$(curl --silent --show-error --output /dev/null --dump-header - \
    --user-agent "$UA" --max-time 25 \
    --data-urlencode "username=${CR8_SMOKE_USER:?set CR8_SMOKE_USER}" \
    --data-urlencode "password=$password" \
    "${origin%/}/login" | grep -i '^set-cookie:' || true)
  if [ -z "$cookie" ]; then
    echo "no session cookie was issued at $origin/login" >&2
    exit 1
  fi
  case "$cookie" in
    *Secure*) ;;
    *)
      echo "session cookie is not marked Secure on a public https origin" >&2
      exit 1
      ;;
  esac
  case "$cookie" in
    *HttpOnly*) ;;
    *)
      echo "session cookie is not HttpOnly" >&2
      exit 1
      ;;
  esac
}

# The Next app answers / with a static shell for anyone; the gate that matters
# is that the shell carries no catalogue in it and every data route refuses.
check_shell_is_empty() {
  path=$1
  status=$(curl --silent --show-error \
    --output "$probe_body" --write-out '%{http_code}' \
    --user-agent "$UA" --max-time 20 "${origin%/}${path}")
  if [ "$status" != "200" ] && [ "$status" != "303" ]; then
    echo "the app shell at $path returned $status" >&2
    exit 1
  fi
  if grep -qiE "bounce_ulid|song_ulid|key_camelot" "$probe_body"; then
    echo "the signed-out shell at $path contains catalogue data" >&2
    exit 1
  fi
}

check_login
check_cookie_is_secure

# Pages the Next app serves. Anyone gets the shell; nobody gets the catalogue
# inside it, and its own gate sends a signed-out visitor to /login.
check_shell_is_empty /
check_shell_is_empty /triage
check_shell_is_empty /activity
check_shell_is_empty /for-you
check_shell_is_empty /upload
check_shell_is_empty /admin

# The legacy Jinja admin pages are deliberately NOT forwarded by the web server,
# so they are unreachable from the public origin at all. The Next /admin page
# replaced them and goes through the API like everything else.

# Everything that carries data has to refuse outright.
check_protected /api/library
check_protected /api/library-queue
check_protected /api/me
check_protected /api/assignments
check_protected /api/uploads
check_protected /api/admin/invites
check_protected /api/admin/members
check_protected /api/admin/tokens

echo "public login and authentication boundary: PASS"
