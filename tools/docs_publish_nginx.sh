#!/usr/bin/env bash
# Publish the built docs site to nginx behind HTTP basic auth. Idempotent.
#   sudo bash tools/docs_publish_nginx.sh [password]
# Build first:  /home/claude/.venvs/docs/bin/python tools/docs_publish.py
#
# Host: ki-workflow.agency (colleague's secondary hostname of the marketing site, valid LE cert).
# We ONLY insert one additive prefix location; his locations (/, = /outlook-signature.html,
# ~ ^/valentyn-signature, acme-challenge) are never touched or shadowed — a prefix match on
# /pflege-docs/ cannot capture any of them. Config is backed up, nginx -t'd, auto-rolled-back,
# and his site is regression-checked after the reload.
set -euo pipefail

BUILD=/home/claude/repo/pflege-board/data/docs-site
DOCROOT=/var/www/pflege-docs
SITE=/etc/nginx/sites-available/ki-workflow.agency
ANCHOR='# Keep Outlook signature assets'   # comment line above his signature location (443 block); keeps his comment attached to his own location
HOSTNAME_PUB=ki-workflow.agency
HTPASSWD=/etc/nginx/.htpasswd-pflege-docs
USER=pflege
PASS="${1:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)}"

[ -d "$BUILD" ] || { echo "no build at $BUILD — run docs_publish.py first"; exit 1; }
grep -q "$ANCHOR" "$SITE" || { echo "anchor not found in $SITE — aborting, insert manually"; exit 1; }

echo "== files =="
mkdir -p "$DOCROOT"
if command -v rsync >/dev/null; then rsync -a --delete "$BUILD"/ "$DOCROOT"/; else rm -rf "${DOCROOT:?}"/*; cp -r "$BUILD"/. "$DOCROOT"/; fi
chown -R www-data:www-data "$DOCROOT"; chmod -R u=rwX,g=rX,o= "$DOCROOT"
echo "copied $(find "$DOCROOT" -name '*.html' | wc -l) html files"

echo "== basic auth =="
printf '%s:%s\n' "$USER" "$(openssl passwd -apr1 "$PASS")" > "$HTPASSWD"
chown root:www-data "$HTPASSWD"; chmod 640 "$HTPASSWD"

echo "== nginx =="
BAK="$SITE.bak.$(date +%Y%m%d-%H%M%S)"
cp -a "$SITE" "$BAK"; echo "backup: $BAK"
if grep -q 'location /pflege-docs/' "$SITE"; then
  echo "location already present, config untouched"
else
  ANCHOR="$ANCHOR" python3 - "$SITE" <<'PY'
import os, sys
p, anchor = sys.argv[1], os.environ["ANCHOR"]
lines = open(p).read().splitlines(True)
block = """    # --- pflege internal docs (additive; does not affect any existing location) ---
    location /pflege-docs/ {
        alias /var/www/pflege-docs/;
        index index.html;
        autoindex off;
        default_type text/html;
        add_header X-Robots-Tag "noindex, nofollow" always;
        auth_basic "Pflege internal docs";
        auth_basic_user_file /etc/nginx/.htpasswd-pflege-docs;
    }

"""
for i, l in enumerate(lines):
    if anchor in l:
        lines.insert(i, block); break
else:
    raise SystemExit("anchor vanished — aborting")
open(p, "w").write("".join(lines))
print("inserted /pflege-docs/ before:", anchor)
PY
fi

if ! nginx -t; then
  echo "!! nginx -t FAILED — rolling back"; cp -a "$BAK" "$SITE"; nginx -t && echo "rolled back OK"; exit 1
fi
systemctl reload nginx

echo "== verify =="
HIS=$(curl -s -o /dev/null -w '%{http_code}' "https://$HOSTNAME_PUB/" || true)
SIG=$(curl -s -o /dev/null -w '%{http_code}' "https://$HOSTNAME_PUB/outlook-signature.html" || true)
NOAUTH=$(curl -s -o /dev/null -w '%{http_code}' "https://$HOSTNAME_PUB/pflege-docs/" || true)
AUTH=$(curl -s -o /dev/null -w '%{http_code}' -u "$USER:$PASS" "https://$HOSTNAME_PUB/pflege-docs/" || true)
echo "colleague site /            : $HIS   (expect 200/301/302)"
echo "colleague /outlook-signature: $SIG   (expect 200/301/302/404 — unchanged)"
echo "/pflege-docs/ without auth  : $NOAUTH (expect 401)"
echo "/pflege-docs/ with auth     : $AUTH   (expect 200)"
if [ "$NOAUTH" != "401" ] || [ "$AUTH" != "200" ] || { [ "$HIS" != "200" ] && [ "$HIS" != "301" ] && [ "$HIS" != "302" ]; }; then
  echo "!! verification failed — rolling back nginx config"; cp -a "$BAK" "$SITE"; nginx -t && systemctl reload nginx && echo "rolled back OK"; exit 1
fi

echo "== done =="
echo "URL : https://$HOSTNAME_PUB/pflege-docs/"
echo "user: $USER"
echo "pass: $PASS"
echo "(password is printed only here — save it)"
