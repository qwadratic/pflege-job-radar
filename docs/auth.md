# Auth – who gets into /pro

Three roles: **owner** (you), **customer** (paid through Stripe), **anonymous** (everyone else on the public share).
The identity is computed once per request by the middleware in `app/auth.py` (`request.state.identity`) and exposed
to the pages by `GET /api/me` – `{role, email, via, login_url, auth_disabled}`. The raw proxy headers are never echoed.

## How the owner gets in

1. **exe.dev login** (default). The exe.dev HTTPS proxy injects `X-ExeDev-Email` / `X-ExeDev-UserID` for logged-in
   exe.dev users only; anonymous visitors on the public share carry no such headers and clients cannot spoof them.
   If the address is in `OWNER_EMAILS` (case-insensitive) the request is `owner` via `exe`. Not logged in yet?
   The gate card on `/pro` links to `https://pflege-board.exe.xyz/__exe.dev/login?redirect=/pro` – the app never
   redirects there by itself.
2. **Tailnet.** With `TAILNET_TRUST=1`, a request whose `request.client.host` is inside `100.64.0.0/10` is `owner` via
   `tailnet`. Off by default (Tailscale is installed on the VM but not joined; do not enable the flag behind a proxy
   that could put a tailnet address into `client.host`).
3. **Magic link** to an owner address (same flow as customers below) – handy from a browser without an exe.dev login.

## How a customer gets in (magic link after Stripe)

Stripe is feature-flagged on `STRIPE_SECRET_KEY`; until it is configured `/api/stripe/*` answers 503 and nobody can
become a customer, so today the gate resolves to owner / anonymous.

1. After a real Stripe Checkout the Stripe track calls `auth.upsert_customer(email, stripe_customer_id)` – one row in
   the `customers` table (`email, stripe_customer_id, status, created_at`). Only `status = 'active'` rows may sign in.
2. The customer opens `/pro`, sees the gate card and enters the e-mail: `POST /api/auth/magic {email}`.
   - always `200 {"sent": true}` – the answer never reveals whether the address is known;
   - `429` after more than 3 requests for the same address within an hour (applied to every address alike);
   - `400` when the body has no e-mail.
   For an owner or an active customer a single-use token (`secrets.token_urlsafe(32)`, only its SHA-256 is stored in
   `magic_links`, 15 min TTL) is mailed through the VM gateway (`POST http://169.254.169.254/gateway/email/send`,
   plain text) as `https://pflege-board.exe.xyz/api/auth/magic/<token>` (`PUBLIC_BASE_URL` overrides the host).
3. `GET /api/auth/magic/<token>` validates (unknown / used / expired -> `400 {"error": "invalid or expired link"}`),
   marks the token used, inserts a `sessions` row (30 days) and answers `303 -> /pro` with the cookie
   `pj_session` (`HttpOnly; Secure; SameSite=Lax; Path=/`). The cookie is `<sid>.<hmac>`; the HMAC uses
   `SESSION_SECRET` (env) or a secret generated once and persisted in the `settings` table (`session_secret`).
   Only the SHA-256 of the sid is stored. The role comes from the session row: `owner` for a link sent to an owner
   address, `customer` otherwise (the Stripe track can also call `auth.create_session(email, "customer", cus_id)` +
   `auth.set_session_cookie(response, value)` right after Checkout).
4. `POST /api/auth/logout` deletes the session row and clears the cookie.

## Agent API key (non-interactive access to a crawl-firing subset)

For an agent with no exe.dev account and no session cookie — e.g. a partner's crawler/reviewer
running off this VM. `PUT /api/settings/agent-key` (owner-login only, add `?rotate=true` to replace
an existing one) generates a key and returns it **once**, in that response only. Only its SHA-256
hash is persisted (`settings.agent_key`, `app/settings.py: set_agent_key/check_agent_key`) — it is
never stored in plaintext, never echoed by `GET /api/settings` (which shows only `{configured,
created_at, rotated_at}`), and never logged. Losing the plaintext means rotating, not recovering it.
`DELETE /api/settings/agent-key` revokes it outright.

Send it as `X-Api-Key: <key>` on a request. It unlocks **only** `app/auth.py: AGENT_WRITE_PREFIXES`
— deliberately a narrow subset of the owner-write matrix below, not owner access:

| unlocked by a valid `X-Api-Key` | stays owner-login-only even with a valid key |
|---|---|
| `POST /api/crawl` | `/api/schedules`, `/api/settings*`, `/api/hunter*`, `/api/scheduler*`, `/api/campaign*` |
| `POST /api/inbox/drain` | (spend controls, kill switches, config — human only) |
| `POST /api/clinics/{id}/refetch-career` | |

The key check is independent of `identity()`/sessions: it does not grant the `owner` role, does not
pass `GET` owner-read gates (`/api/billing`, `/api/coverage`, …), and a wrong or missing key falls
through to the normal 401. `app/auth.py: agent_key_ok()`/`agent_write_allowed()` are the single
source of both the subset and the check, mirrored in `tests/test_auth.py`'s agent-key tests.

**Gate page door** ("I'm an agent →" on the `/pro` login card): `POST /api/auth/agent {key}`
(`api_auth_agent`) checks the same key and, on success, answers `{ok, skill_url: "/skill/SKILL.md"}`
-- no cookie, no session, no dashboard. The gate's JS navigates straight to that URL, which
`GET /skill/{name}` already serves unauthenticated. The check exists for the login UX (a wrong key
gets an error, not a silent redirect to a page that needs no auth anyway) -- it never grants owner
or customer role. `skill/SKILL.md` tells the agent to work API-level and only load the board
frontend when a human explicitly asks it to look at the UI itself.

**Never paste a minted key into a chat/agent transcript.** `tools/mint_kindt_env.py [--rotate]`
mints or rotates the key and writes it straight into `.env.kindt` (gitignored) -- it never prints
the value, so it can't leak into a log or a session transcript the way a `PUT
/api/settings/agent-key` response pasted into chat would.

## What the middleware enforces

| who | may |
|---|---|
| owner only | `POST/PUT/PATCH/DELETE` under `/api/crawl`, `/api/schedules`, `/api/settings`, `/api/inbox/drain`, `/api/hunter`, `/api/scheduler`, `/api/clinics/{id}/refetch-career`; `GET /api/billing*`, `/api/hunter*`, `/api/settings*`, `/api/coverage`, `/api/inbox` |
| owner or customer | `GET /pro`, `/pro/`, `/autopilot` – the HTML is served to everyone anyway; the SPA reads `/api/me` and shows the gate card instead of the dashboard |
| everyone | the public board `/`, `/api/clinics`, `/api/jobs`, `/api/search`, `/api/cv`, `/api/stats`, `/api/crawl/runs` (read), `/api/schedules` (read), `/api/firecrawl/webhook` (own secret), `/api/stripe/webhook`, `/api/auth/*`, `/api/me` |

Unauthorised API call -> `401 {"error": "owner only" | "sign in required", "role": ..., "login_url": ...}`.
Unauthorised page -> the page, never a 302 to the exe.dev login (the card offers it). `required_role(method, path)`
in `app/auth.py` is the single source of the matrix.

## Env vars

| var | default | meaning |
|---|---|---|
| `OWNER_EMAILS` | `ukraine.bz1@gmail.com,ivan.d.kotelnikov@gmail.com` | comma list of owner addresses (case-insensitive) |
| `SESSION_SECRET` | generated, stored in `settings.session_secret` | HMAC key for the `pj_session` cookie; rotating it logs everyone out |
| `TAILNET_TRUST` | unset | `1` = requests from `100.64.0.0/10` are the owner |
| `AUTH_DISABLED` | unset | `1` = everyone is `owner` via `disabled`, nothing is enforced. Local dev and tests only (`tests/conftest.py` sets it; `tests/test_auth.py` switches it off per test). Never set it on the VM. |
| `PUBLIC_BASE_URL` | `https://pflege-board.exe.xyz` | host in the mailed magic link |
| `STRIPE_SECRET_KEY` | unset | Stripe track; without it no customer can ever be created |

## Tables (`app/runs.py` SCHEMA, SQLite `data/app.sqlite`)

- `magic_links(id, email, token_hash, role, created_at, expires_at, used_at)` – one row per request, also for unknown
  addresses (`role = 'none'`, never mailed) so the per-address rate limit is uniform and survives restarts.
- `sessions(sid_hash, email, role, created_at, expires_at, last_seen_at, stripe_customer_id)`.
- `customers(email, stripe_customer_id, status, created_at)` – filled by the Stripe track.
