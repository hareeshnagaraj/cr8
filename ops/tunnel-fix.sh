#!/bin/zsh
# Repair a tunnel that was authorised against the WRONG Cloudflare account.
#
# `cloudflared tunnel login` refuses to overwrite an existing ~/.cloudflared/cert.pem
# and exits non-zero, but the commands after it happily reuse that stale cert. If
# the cert belongs to a different account, the tunnel and its DNS records are
# created over there instead, which is how cr8.li ended up as a CNAME named
# "cr8.li.geocastdev.work" inside someone else's zone.
#
# This moves the old cert aside, forces a fresh login, and rebuilds the tunnel in
# the account that actually owns cr8.li.
set -uo pipefail

DOMAIN="${CR8_DOMAIN:-cr8.li}"
TUNNEL="cr8"
PORT=3100
CF="$HOME/.cloudflared"

echo "stopping the misconfigured service"
sudo cloudflared service uninstall 2>&1 | tail -1
echo
echo "browser opens now: pick $DOMAIN from the zone list"
cloudflared tunnel login || { echo "login failed"; exit 1; }

[[ -f "$CF/cert.pem" ]] || { echo "no new cert written, aborting"; exit 1; }

echo "creating the tunnel in the correct account"
cloudflared tunnel create "$TUNNEL" 2>&1 | tail -2

TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | awk -v t="$TUNNEL" '$2==t {print $1}')
[[ -n "$TUNNEL_ID" ]] || { echo "could not resolve tunnel id"; exit 1; }

echo "routing $DOMAIN"
cloudflared tunnel route dns --overwrite-dns "$TUNNEL" "$DOMAIN" 2>&1 | tail -1
cloudflared tunnel route dns --overwrite-dns "$TUNNEL" "www.$DOMAIN" 2>&1 | tail -1

cat > "$CF/config.yml" <<YAML
tunnel: $TUNNEL_ID
credentials-file: $CF/$TUNNEL_ID.json

ingress:
  - hostname: $DOMAIN
    service: http://127.0.0.1:$PORT
  - hostname: www.$DOMAIN
    service: http://127.0.0.1:$PORT
  - service: http_status:404
YAML

echo "installing the service"
sudo cloudflared service install 2>&1 | tail -1

echo
echo "verify:"
echo "  dig +short $DOMAIN            # expect a CNAME to $TUNNEL_ID.cfargotunnel.com"
echo "  curl -I https://$DOMAIN"
echo
echo "then delete these two leftovers in the geocastdev.work zone:"
echo "  cr8.li.geocastdev.work"
echo "  www.cr8.li.geocastdev.work"
