#!/bin/zsh
# Put cr8 on https://cr8.li through a Cloudflare Tunnel.
#
# The tunnel dials OUT from this Mac to Cloudflare, so nothing is exposed: no
# port forwarding, no open inbound ports, and your home IP never appears in DNS.
# Cloudflare terminates TLS and hands traffic to the Next app on 127.0.0.1:3100,
# which already proxies /api, /m, /peaks and /art to the Python app on 8080.
#
# Run this ONCE, after cr8.li's nameservers are pointed at Cloudflare.
#
#   scripts/tunnel-setup.sh
set -uo pipefail

DOMAIN="${CR8_DOMAIN:-cr8.li}"
TUNNEL="cr8"
PORT=3100

command -v cloudflared >/dev/null 2>&1 || {
  echo "cloudflared missing: brew install cloudflared"; exit 1
}

# Is the domain actually on Cloudflare yet? Routing before the nameservers flip
# fails with an error that reads like a permissions problem, which it is not.
ns=$(dig +short NS "$DOMAIN" | head -1)
if [[ "$ns" != *cloudflare* ]]; then
  echo "$DOMAIN is not on Cloudflare nameservers yet (currently: ${ns:-none})."
  echo "Point the nameservers at Cloudflare in your registrar, then re-run."
  exit 1
fi

echo "1/4  browser login (authorises this machine for $DOMAIN)"
cloudflared tunnel login || exit 1

echo "2/4  create the tunnel"
cloudflared tunnel create "$TUNNEL" 2>/dev/null || echo "     (already exists)"

echo "3/4  point $DOMAIN at it"
cloudflared tunnel route dns --overwrite-dns "$TUNNEL" "$DOMAIN" 2>&1 | tail -1
cloudflared tunnel route dns --overwrite-dns "$TUNNEL" "www.$DOMAIN" 2>&1 | tail -1

mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<YAML
tunnel: $TUNNEL
credentials-file: $HOME/.cloudflared/$(cloudflared tunnel list 2>/dev/null | awk -v t="$TUNNEL" '$2==t {print $1}').json

ingress:
  - hostname: $DOMAIN
    service: http://127.0.0.1:$PORT
  - hostname: www.$DOMAIN
    service: http://127.0.0.1:$PORT
  - service: http_status:404
YAML

echo "4/4  install as a login service so it survives reboots"
sudo cloudflared service install 2>&1 | tail -2

echo
echo "done. https://$DOMAIN"
echo "check with: cloudflared tunnel info $TUNNEL"
