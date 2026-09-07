#!/usr/bin/env bash
set -euo pipefail
B='https://supabase.int.exe.xyz/rest/v1'; H='Accept-Profile: pflege_jobs'
cnt(){ curl -sI -H "$H" -H 'Prefer: count=exact' "$B/$1" | tr -d '\r' | sed -n 's/.*content-range: .*\///p'; }
open=$(cnt 'postings?select=posting_id&status=eq.open')
unlinked=$(cnt 'postings?select=posting_id&status=eq.open&clinic_id=is.null')
inbox=$(cnt 'inbox?select=inbox_id&processed_at=is.null')
for off in 0 1000 2000 3000; do curl -s -H "$H" "$B/postings?select=clinic_id&status=eq.open&clinic_id=not.is.null&order=posting_id&limit=1000&offset=$off"; done > /tmp/metric_clinics.json
clinics=$(python3 -c "import re;s=open('/tmp/metric_clinics.json').read();print(len(set(re.findall(r'\"clinic_id\":\"([^\"]+)\"',s))))")
routable=$(curl -s http://localhost:8501/api/stats | python3 -c 'import json,sys;print(json.load(sys.stdin)["clinics_routable"])')
echo "clinics_with_open=$clinics open=$open open_unlinked=$unlinked inbox_unprocessed=$inbox routable=$routable"
