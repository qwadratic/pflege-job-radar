# Auth – who gets into /pro, /autopilot and /deck

Three roles: **owner** (you, through one shared login), **customer** (paid through Stripe, magic link),
**anonymous** (everyone else). The identity is computed once per request by the middleware in `app/auth.py`
(`request.state.identity`) and exposed to the pages by `GET /api/me`:
`{role, email, via, login_url, auth_disabled, default_credentials}`.

`via` is one of `disabled` | `session` | `none`. There is no fourth value: the exe.dev proxy-header door and
the tailnet door were **deleted** (2026-09-10). Anything that can reach the uvicorn port directly
(`deploy/pflege-web.service:9` binds `0.0.0.0:8501`) could set `X-ExeDev-Email` itself, so that header was a
forge hole rather than a login. It is not kept as a fallback anywhere.

## Read this before you deploy

**A default owner user/passphrase pair is seeded, and it is short and guessable — treat it as already known.**
This file is served publicly (`GET /docs/auth.md`, no auth), so it does not print the pair; read it off
`app/auth.py: DEFAULT_LOGIN` on the box. **Change it before the box is reachable from the public internet.**
Until you do, anyone who can reach the port owns the board. Three things say so out loud:

1. a `WARNING:` line on every startup (`warn_default_credentials()`, registered by `install()`) — the only
   place the pair is ever printed, into the service log, not into an HTTP response,
2. `GET /api/me` returns `"default_credentials": true`,
3. the login page and the deck render a red banner while that flag holds.

Change it with `PUT /api/auth/password` (see below). That clears the flag and silences all three.

### Rate limits

| door | limit | key | counts |
|---|---|---|---|
| `POST /api/auth/magic` | `MAGIC_PER_HOUR = 3` per hour | the e-mail address | **every** request, wanted or not |
| `POST /api/auth/login` | `LOGIN_FAIL_PER_HOUR = 10` per hour | `request.client.host` | wrong guesses only |

Over the magic-link limit → `429 application/problem+json` (`#quota-exceeded`) until the hour rolls. That
counter is per e-mail address, so one address can never cost another one its link.

Over the login limit → plain `429 {"error": "..."}` (same shape as the magic-link 429, see "Not covered yet"
below) until the hour rolls for that address. The counter is per source IP (`app/auth.py: login_failures`
table), and only wrong guesses add to it — a right guess never counts, so it never throttles itself. Shipped
2026-09-11 as design 1 below, now that the raw TCP peer is known-trustworthy: see the paragraph after the
table.

#### The login throttle that shipped on 2026-09-10 and was reverted on 2026-09-11

**What was tried.** `LOGIN_PER_HOUR = 10`: a rolling one-hour count of *wrong* `POST /api/auth/login`
attempts, kept as a list of ISO timestamps in `settings.login_failures`, and a `429` for everyone — right
guesses included — once the count passed ten. It was argued for as "the rule the magic-link door beside it
already had, applied to the passphrase door".

**Why it was wrong.** The magic-link bucket is keyed on the e-mail address; this one had nothing to key on,
because there is exactly **one shared credential** and (see below) no usable client IP, so it was a single
global bucket. One shared credential + one global bucket = a denial of service handed to every anonymous
caller. Measured against the code as it shipped:

```
60 wrong passphrases in 0.33 s  ->  [401 x10, 429 x50]
the CORRECT user/pass, immediately after  ->  429, and for the next hour
```

Ten requests from anyone — a scanner, a bored visitor, a broken retry loop — locked the owner out of the only
deployed sign-in door for an hour, and the row survived a restart, so restarting the service did not clear it.
The magic link was named as the owner's second door, but it needs a working mail gateway and an `OWNER_EMAILS`
inbox: that is a fallback, not a door that is *always* there. Cost: certain, anonymous, repeatable. Benefit:
ten guesses an hour instead of unlimited, against a passphrase an attacker gets unlimited offline tries at the
moment they have the SQLite file anyway. Reverted: `LOGIN_PER_HOUR`, `_login_failures()` and the
`settings.login_failures` row are gone — replaced the same day by the per-IP design below, a different table
(`login_failures`, not the `settings` row) and a different key (IP, not one global counter). A
`settings.login_failures` row left over in an existing `data/app.sqlite` is inert — nothing reads or writes
it any more.

**The two designs that would work.** Ivan picked the first; it shipped 2026-09-11.

1. **Per-IP bucket — shipped 2026-09-11.** Correct only once the app sees a real client IP. It was assumed
   `request.client.host` would be the exe.dev proxy for every remote caller (same global bucket wearing a
   hat) — checked instead against the live access log (`journalctl -u pflege-web`, source IPs on real
   requests) and against Ivan directly: port 8501 (`deploy/pflege-web.service:11`) is **directly
   internet-reachable**, not fronted by a proxy that terminates the connection and sets a trustworthy
   `X-Forwarded-For`. So `request.client.host` already is the real caller for every request, no
   `--proxy-headers` deploy change and no forwarded header needed — `LOGIN_FAIL_PER_HOUR = 10` wrong guesses
   per hour, counted in the `login_failures` table (`ip`, `at`), never on a right guess. An attacker with many
   source addresses still gets many buckets, which is the honest limit of this design.
2. **Constant delay, no lockout — not shipped.** Sleep a fixed ~1 s on every wrong attempt (never on a right
   one, and never an increasing or per-caller delay). That cuts an online guessing run by roughly two orders
   of magnitude and there is no state to exhaust, so there is nothing for an anonymous caller to spend on the
   owner's behalf. The cost is one held connection per wrong attempt, which is itself a small amount of rope:
   with a hard worker limit that becomes a different denial of service, so it wants an async sleep and a look
   at the worker count first. Could still be added alongside the per-IP bucket; Ivan's call.

Still open, same category: one shared credential means no per-user identity, no audit trail of who tried, and
no revocation short of changing the one secret. A real user table is a bigger decision than a counter.

Related, same category: one shared credential means no per-user identity and no revocation short of changing
the one secret. `PUT /api/auth/password` does **not** invalidate existing sessions (they are keyed on the
cookie HMAC, not the passphrase) — to kick everyone out, delete the `sessions` rows or rotate `SESSION_SECRET`.

## How the owner gets in

`POST /api/auth/login {"user": "...", "pass": "..."}`

- `200 {"ok": true, "default_credentials": true|false}` + the session cookie `pj_session`
  (`HttpOnly; Secure; SameSite=Lax; Path=/`, 30 days) → `owner` via `session`;
- `401 application/problem+json` otherwise. The answer never says which half was wrong.

Only SHA-256 hashes are ever persisted, in the settings table under the key `login`
(`{user_hash, pass_hash, is_default}`, `app/auth.py: login_credentials/check_login/set_login` – the same shape
as the agent key in `app/settings.py`). The plaintext is never stored, never logged, never echoed. **No row
at all means the shipped defaults are still in force** – `login_credentials()` returns them with
`is_default: true` and writes nothing.

`PUT /api/auth/password {"user": "...", "pass": "..."}` (owner session only) replaces both halves at once and
clears `is_default`. `400` when either half is empty. There is no "old password" field: whoever can call it
already holds an owner session, which is the same power. It is in `OWNER_WRITE_PATHS`, deliberately **not**
reachable with an agent key — the crawl door must not be able to lock the owner out.

`POST /api/auth/logout` deletes the session row and clears the cookie.

**A magic link to an `OWNER_EMAILS` address also produces an owner session** (same flow as customers below).
`OWNER_EMAILS` is nothing else any more: it is the magic-link role decision (`customer_role()`), not a door.

## How a customer gets in (magic link after Stripe)

Stripe is feature-flagged on `STRIPE_SECRET_KEY`; until it is configured `/api/stripe/*` answers 503 and nobody
can become a customer, so today the gate resolves to owner / anonymous.

1. After a real Stripe Checkout the Stripe track calls `auth.upsert_customer(email, stripe_customer_id)` – one
   row in the `customers` table (`email, stripe_customer_id, status, created_at`). Only `status = 'active'`
   rows may sign in.
2. The customer enters the e-mail: `POST /api/auth/magic {email}`.
   - always `200 {"sent": true}` – the answer never reveals whether the address is known;
   - `429` after more than 3 requests for the same address within an hour (applied to every address alike);
   - `400` when the body has no e-mail.
   For an owner or an active customer a single-use token (`secrets.token_urlsafe(32)`, only its SHA-256 is
   stored in `magic_links`, 15 min TTL) is mailed through the VM gateway
   (`POST http://169.254.169.254/gateway/email/send`, plain text) as
   `https://pflege-board.exe.xyz/api/auth/magic/<token>` (`PUBLIC_BASE_URL` overrides the host).
3. `GET /api/auth/magic/<token>` validates (unknown / used / expired → `400 {"error": "invalid or expired
   link"}`), marks the token used, inserts a `sessions` row (30 days) and answers `303 → /pro` with the cookie.
   The cookie is `<sid>.<hmac>`; the HMAC uses `SESSION_SECRET` (env) or a secret generated once and persisted
   in the settings table (`session_secret`). Only the SHA-256 of the sid is stored. The role comes from the
   session row: `owner` for a link sent to an owner address, `customer` otherwise (the Stripe track can also
   call `auth.create_session(email, "customer", cus_id)` + `auth.set_session_cookie(response, value)` straight
   after Checkout). A cancelled subscription revokes the session at the next request.

## The `Secure` cookie and local development

`set_session_cookie()` sets `Secure`, so **a login over plain `http://` does not stick** – the browser accepts
the response and then never sends the cookie back. In production everything is HTTPS, so this only bites
locally: run with `AUTH_DISABLED=1` (below), or put the dev server behind TLS. Tests must use
`base_url="https://testserver"` (`tests/test_auth.py`). Making it `secure=not auth_disabled()` is a one-word
change that nobody has asked for – open question, not a decision.

## Agent API key (non-interactive access to a crawl-firing subset)

For an agent with no session cookie — e.g. a partner's crawler/reviewer running off this VM.
`PUT /api/settings/agent-key` (owner-login only, add `?rotate=true` to replace an existing one) generates a key
and returns it **once**, in that response only. Only its SHA-256 hash is persisted (`settings.agent_key`,
`app/settings.py: set_agent_key/check_agent_key`) — never stored in plaintext, never echoed by
`GET /api/settings` (which shows only `{configured, created_at, rotated_at}`), never logged. Losing the
plaintext means rotating, not recovering it. `DELETE /api/settings/agent-key` revokes it outright.

Send it as `X-Api-Key: <key>` on a request. Each key carries a set of **scopes** (`app/auth.py: SCOPES`), and a
scope opens exactly the routes `AGENT_ROUTES` lists for it and nothing else — deliberately a narrow subset of
the matrix below, not owner access. `GET /api/agent/manifest` is generated from that table, so the published
contract and the middleware cannot drift apart; read it there rather than from a copy here.

Never scopable (simply absent from `AGENT_ROUTES`), and owner session only: `/api/settings*`, `/api/hunter*`,
`/api/scheduler*`, `/api/autocrawl/tick`, `/api/campaign`, `/api/schedules` writes, `/api/mechanics/*/try|test`,
`/api/billing*`, `/api/autopilot*`, `POST /api/refresh-cache`, `PUT /api/auth/password` (it must not be able to
lock the owner out) — spend controls, kill switches, config, credentials, billing. Never scopable but **not**
owner-only: `POST /api/postings/{id}/closed` is `member` (`MEMBER_API`), and `/api/stripe/*` is public —
`required_role()` returns `None` for `POST /api/stripe/checkout`, `GET /api/stripe/status` and
`POST /api/stripe/webhook`. That is the code being right and this file having been wrong until 2026-09-10:
checkout is how a visitor who is not a customer yet pays (owner-gating it would mean nobody can ever buy), and
the webhook is called by Stripe and authenticates itself with the `stripe-signature` HMAC
(`verify_signature()`), not with a session. `GET /api/stripe/status` is the one to look at again: it needs no
session and returns `customers` and `closed_this_period` counts to anonymous callers — a revenue-shaped leak,
small, and nobody has decided it should be gated, so it is recorded here rather than narrowed unilaterally.

The key check is independent of `identity()`/sessions: it does not grant the `owner` role and a wrong or missing
key falls through to the normal 401. A valid key on a route with **no** `AGENT_ROUTES` entry also gets the plain
401 (there is no scope to go and ask for); a valid key that is merely missing the right scope gets
`403 application/problem+json` with a `scope` extension naming what to ask its operator for
(`insufficient_scope()`). `app/auth.py: agent_key_ok()`/`agent_allowed()` are the single source of both the
subset and the check, mirrored in `tests/test_auth.py` and `tests/test_agent_api.py`.

**Gate page door** ("I'm an agent →" on the login card): `POST /api/auth/agent {key}` checks the same key and,
on success, answers `{ok, skill_url: "/skill/SKILL.md"}` -- no cookie, no session, no dashboard. The gate's JS
navigates straight to that URL, which `GET /skill/{name}` already serves unauthenticated. The check exists for
the login UX (a wrong key gets an error, not a silent redirect to a page that needs no auth anyway) — it never
grants owner or customer role. `skill/SKILL.md` tells the agent to work API-level and only load the board
frontend when a human explicitly asks it to look at the UI itself.

**Never paste a minted key into a chat/agent transcript.** `tools/mint_kindt_env.py [--rotate]` mints or
rotates the key and writes it straight into `.env.kindt` (gitignored) — it never prints the value.

## What the middleware enforces

`required_role(method, path)` in `app/auth.py` is the single source of this matrix.

| who | may |
|---|---|
| owner only | `POST/PUT/PATCH/DELETE` under `/api/crawl`, `/api/schedules`, `/api/settings`, `/api/inbox/drain`, `/api/ingest`, `/api/hunter`, `/api/scheduler`, `/api/campaign`, `/api/autopilot`, `/api/autocrawl`, `/api/mechanics`, `/api/refresh-cache` (`OWNER_WRITE_PREFIXES`), plus `POST /api/clinics/{id}/refetch-career` (`OWNER_WRITE_RE`) and `PUT /api/auth/password` (`OWNER_WRITE_PATHS`); `GET` under `/api/billing`, `/api/hunter`, `/api/settings`, `/api/coverage`, `/api/inbox`, `/api/firecrawl`, `/api/crawl` (**including `GET /api/crawl/runs` and `/api/crawl/plan`**), `/api/campaign`, `/api/autopilot`, `/api/schedules` (`OWNER_READ_PREFIXES`); the page `/deck`, `/deck/` (`OWNER_PAGES`) |
| owner or customer ("member") | the pages `/pro`, `/pro/`, `/autopilot`, `/autopilot/` (`GATED_PAGES`); writes under `/api/cv` and `/api/postings` (`MEMBER_API` — `POST /api/postings/{id}/closed`); `GET /api/postings/{id}/closed` (`MEMBER_READ`: the row names the address that closed the posting) |
| everyone | `GET /`, `GET /login`, `GET /health`, `GET /dock.css`, `GET /dock.js`, `GET /docs/{name}`, `GET /docs/krankenhausplan_2026.pdf`, `GET /skill/{name}`, `GET /api/docs`, `GET /api/stats`, `GET /api/facets`, `GET /api/taxonomy`, `GET /api/ontology`, `GET /api/cities`, `GET /api/plan`, `GET /api/search`, `GET /api/clinics`, `GET /api/clinics/{clinic_id}`, `GET /api/jobs`, `GET /api/jobs/{posting_id}`, `GET /api/mechanics`, `GET /api/mechanics/{mid}`, `GET /api/flags`, `GET /api/me`, `GET /api/ingest/schemas`, `GET /api/agent/manifest`, `POST /api/auth/login`, `POST /api/auth/logout`, `POST /api/auth/magic`, `GET /api/auth/magic/{token}`, `POST /api/auth/agent`, `POST /api/firecrawl/webhook` (own shared secret), `GET /api/stripe/status`, `POST /api/stripe/checkout`, `POST /api/stripe/webhook` (Stripe's own signature) |

That row is **generated from `required_role()`**, not written by hand: `tests/test_auth.py: test_everyone_row_matches_required_role` walks every path in `app.openapi()` and fails when a route that answers to anonymous callers is missing from the row above (or a row entry stops being public). `GET /api/openapi.json` and `GET /api/openapi-ui` are public too and are the two routes the walk cannot see — the spec does not list itself.

Two of those look surprising and are deliberate: `GET /api/mechanics`/`{mid}` publish the mechanics catalogue (the `try`/`test` writes that shell out to pytest are owner-only), and the `/api/stripe` routes are how someone who is *not* a customer yet pays (§Agent API key above). `POST /api/refresh-cache` **left this row on 2026-09-10**: it calls `data.refresh()` → `_build()`, a full Supabase re-pull with the service key, so any anonymous caller could make this box hammer Supabase in a loop. It is an owner write now, and not scopable — `data/repair_split_merged.py:236` is its one caller.

`GET /api/schedules`, `/api/schedules/presets` and `/api/schedules/{id}/preview` **left the public list on
2026-09-10** — they published every target, mode and `max_credits` to anonymous callers. They are owner reads
now, or `read:ops` with an agent key.

`GET /api/clinics/{clinic_id}` stays public, but **its `runs` array became owner-only on 2026-09-11.** The
route built `runs` from the same `R.list_runs(200)` that `GET /api/crawl/runs` answers `401` for below owner,
and — unlike the `jobs` array on the line above it — never passed it through `D.redact()`, so per-run
Firecrawl `credits_used`, the internal `error` string and the last three `run_log` lines were public. A public
clinic page needs *when this clinic was last crawled*, and that is already on the clinic row itself
(`last_crawl_at` / `last_crawl_status` / `last_crawl_mode`, `app/data.py:183`). So the key stays in the
published shape and is the empty list below owner: there is no field of a run a non-owner was ever shown.
Covered for all three roles by `tests/test_auth.py: test_clinic_detail_publishes_runs_to_the_owner_only`.
This is a **field**-level gate inside a public route, which `required_role()` cannot express — the only other
one is `GET /api/stats`'s `firecrawl` block (owner: full spend, customer: remaining/plan, anonymous: `null`)
and the `D.redact()` pass on every job row.

`POST /api/postings/{id}/closed` and its `GET` used to enforce membership inside the handler in
`app/stripe_gate.py`, which made the sentence above false. They were moved into `required_role()` on
2026-09-10; the handlers no longer check a role at all.

- **Unauthorised API call** → `401 application/problem+json`:
  `{type, title, status, detail, instance, error, role, login_url}` — the RFC 9457 shape `docs/errors.md`
  describes, with `role` and `login_url` added so a page can tell "sign in" from "signed in as the wrong role".
  `detail` is `owner only` or `sign in required`; `error` repeats it for the older template code.
- **Unauthorised page** → `303` to `/login?next=<path>`. This is new: the pages used to be served to everyone
  and left the SPA to draw a gate card, which shipped the whole document to people who may not read it.
  `/login` is not in `GATED_PAGES`/`OWNER_PAGES`, so the redirect cannot loop. A signed-in **customer** asking
  for `/deck` gets that same `303`, not a `403`: there is one page answer for "you may not read this", the
  login card, and it is where a session of the wrong role is told to become another one. A 403 would need a
  second page-shaped response nobody asked for.
- `AUTH_DISABLED=1` short-circuits all of it before the matrix is consulted.

The public OpenAPI spec (`/api/openapi.json`) still enumerates every owner-only route. That is intentional: the
agentic API depends on the spec being public. A passphrase hides data, not the API surface.

## CV upload

The page carrying the upload form stays public; `POST /api/cv` needs a session (`MEMBER_API`). The anonymous
hero CTA therefore sends the visitor to `/login?next=…` instead of 401ing in place. `POST /api/cv` persists
nothing — the parse happens in memory (`app/cv.py`). If the upload should be open to everyone again, delete the
`MEMBER_API` line; nothing else changes.

## Env vars

| var | default | meaning |
|---|---|---|
| `OWNER_EMAILS` | the maintainer's address, `app/auth.py: DEFAULT_OWNER` | comma list of addresses that get an **owner** session when they consume a magic link (case-insensitive). Not a login door on its own. The literal is deliberately not written here: this file is served publicly at `GET /docs/auth.md`, and printing the address turns the owner door into a named target (and a spam address) |
| `SESSION_SECRET` | generated, stored in `settings.session_secret` | HMAC key for the `pj_session` cookie; rotating it logs everyone out |
| `AUTH_DISABLED` | unset | `1` = everyone is `owner` via `disabled`, nothing is enforced. Local dev and tests only (`tests/conftest.py` sets it; `tests/test_auth.py` switches it off per test). Never set it on the VM |
| `PUBLIC_BASE_URL` | `https://pflege-board.exe.xyz` | host in the mailed magic link |
| `STRIPE_SECRET_KEY` | unset | Stripe track; without it no customer can ever be created |

The login credentials are deliberately **not** an env var: they live in the settings table like `agent_key` and
`session_secret`, so `.env` (which `deploy/pflege-web.service:10` reads) holds no plaintext passphrase.
`TAILNET_TRUST` is gone — no code reads it any more.

## Tables (`app/runs.py` SCHEMA, SQLite `data/app.sqlite`)

- `settings.login` – `{user_hash, pass_hash, is_default}`; absent while the defaults are in force.
- `magic_links(id, email, token_hash, role, created_at, expires_at, used_at)` – one row per request, also for
  unknown addresses (`role = 'none'`, never mailed) so the per-address rate limit is uniform across restarts.
- `sessions(sid_hash, email, role, created_at, expires_at, last_seen_at, stripe_customer_id)`.
- `customers(email, stripe_customer_id, status, created_at)` – filled by the Stripe track.

Restoring `data/app.sqlite` from a backup restores the credentials with it. Deleting it resets the login to the
seeded default **and** logs everyone out (the generated `session_secret` goes with it) — so a restore-from-scratch
puts the guessable pair back and you have to run `PUT /api/auth/password` again.
