#!/usr/bin/env bash
# Run this yourself, once, on kindt's machine (or any empty box) BEFORE handing the .env over for
# real use. Loads .env.kindt (copy of .env.kindt.example with real values filled in) from the
# current directory, exercises every door kindt is supposed to have -- and a couple it should NOT
# have -- and prints PASS/FAIL per check. No repo checkout needed: only curl + the env file.
#
#   python tools/mint_kindt_env.py   # writes ./.env.kindt with a real key -- never prints it
#   bash tools/kindt_healthcheck.sh  # from kindt's machine: scp/copy .env.kindt over first
#
# Not a dry run: it inserts one throwaway probe row into the live inbox (kind=probe, never becomes
# a posting, harmless) and queues one real adapter-mode crawl (softgarden, free -- no Firecrawl
# credits spent) so the crawl-firing path is proven end to end, not just reachable.
set -u
ENV_FILE="${1:-.env.kindt}"
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE -- copy .env.kindt.example, fill it in, pass the path as \$1 if not ./.env.kindt"; exit 1; }
set -a; . "$ENV_FILE"; set +a

pass=0; fail=0
check() {  # check "label" expected_status actual_status [grep_pattern] [body]
  local label="$1" want="$2" got="$3" pat="${4:-}" body="${5:-}"
  if [ "$got" != "$want" ]; then echo "FAIL $label -- want HTTP $want, got $got"; fail=$((fail+1)); return; fi
  if [ -n "$pat" ] && ! grep -q "$pat" <<<"$body"; then echo "FAIL $label -- HTTP $got but body missing '$pat': ${body:0:200}"; fail=$((fail+1)); return; fi
  echo "PASS $label"; pass=$((pass+1))
}

echo "== reads: App API, no auth needed =="
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/stats"); check "GET /stats" 200 "$r" '"jobs_open"' "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/facets"); check "GET /facets" 200 "$r" '"ats"' "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/jobs?limit=5"); check "GET /jobs?limit=5" 200 "$r" '"rows"' "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/search?q=intensiv"); check "GET /search?q=" 200 "$r" '"rows"' "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/clinics?ats_type=softgarden&limit=3"); check "GET /clinics?ats_type=" 200 "$r" "" "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' "$PFLEGE_BOARD_API/mechanics"); check "GET /mechanics" 200 "$r" "" "$(cat /tmp/kh_body)"

echo "== reads: PostgREST, anon key =="
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -H "apikey: $SUPABASE_ANON_KEY" -H 'Accept-Profile: pflege_jobs' \
  "$SUPABASE_URL/rest/v1/v_postings?limit=1&select=posting_id,title")
check "GET rest/v1/v_postings" 200 "$r" '"posting_id"' "$(cat /tmp/kh_body)"

echo "== write door open to the public anon key (inbox insert, no secret) =="
probe_url="https://kindt-healthcheck.invalid/probe-$(date +%s)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$SUPABASE_URL/rest/v1/inbox" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  -H 'Content-Profile: pflege_jobs' -H 'Content-Type: application/json' -H 'Prefer: return=minimal' \
  -d "[{\"kind\":\"probe\",\"source_host\":\"kindt-healthcheck.invalid\",\"source_url\":\"$probe_url\",\"payload\":{\"probe\":\"healthcheck\"},\"collector\":\"kindt-healthcheck\"}]")
check "POST rest/v1/inbox (probe row)" 201 "$r" "" "$(cat /tmp/kh_body)"

echo "== agent key: unlocks the crawl-firing subset =="
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$PFLEGE_BOARD_API/inbox/drain" -H "X-Api-Key: $AGENT_API_KEY")
check "POST /inbox/drain with agent key" 200 "$r" "" "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$PFLEGE_BOARD_API/crawl/plan?scope=ats_type&values=softgarden&mode=adapter"); check "GET /crawl/plan (no auth, read)" 200 "$r" "" "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$PFLEGE_BOARD_API/crawl" -H "X-Api-Key: $AGENT_API_KEY" -H 'Content-Type: application/json' \
  -d '{"target":{"scope":"ats_type","values":["softgarden"]},"mode":"adapter","max_credits":0}')
check "POST /crawl with agent key" 200 "$r" '"run_id"' "$(cat /tmp/kh_body)"

echo "== agent key must NOT unlock the control plane =="
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X PUT "$PFLEGE_BOARD_API/settings/firecrawl" -H "X-Api-Key: $AGENT_API_KEY" -H 'Content-Type: application/json' -d '{}')
check "PUT /settings/firecrawl with agent key (must be 401)" 401 "$r" "" "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$PFLEGE_BOARD_API/hunter/start" -H "X-Api-Key: $AGENT_API_KEY")
check "POST /hunter/start with agent key (must be 401)" 401 "$r" "" "$(cat /tmp/kh_body)"
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -X POST "$PFLEGE_BOARD_API/crawl" -H 'Content-Type: application/json' -d '{"target":{"scope":"ats_type","values":["softgarden"]}}')
check "POST /crawl with NO key (must be 401)" 401 "$r" "" "$(cat /tmp/kh_body)"

echo "== CV endpoint (public, no auth) =="
r=$(curl -s -o /tmp/kh_body -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"text":"Pflegefachkraft, 6 Jahre Intensivstation, München, B2"}' "$PFLEGE_BOARD_API/cv")
check "POST /cv (plain text)" 200 "$r" '"profile"' "$(cat /tmp/kh_body)"

rm -f /tmp/kh_body
echo; echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
