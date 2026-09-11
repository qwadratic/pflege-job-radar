# Errors — RFC 9457 problem details

Every error the API produces through the two exception handlers in `app/main.py` is
`application/problem+json`:

```json
{
  "type": "/docs/errors.md#unknown-clinic",
  "title": "Unknown clinic",
  "status": 404,
  "detail": "unknown clinic",
  "instance": "/api/clinics/99999",
  "error": "unknown clinic"
}
```

- `type` — the identifier to match on. It points back at this file, whose table below is the index of every
  type (the fragment names the type; it is not a rendered heading anchor). `about:blank` when the status code
  alone says everything there is to say (RFC 9457 §4.2.1); the shape of the body is the same either way.
- `title` — a short, stable label for the type. It never varies with the occurrence, so match on `type`
  (or on `status`), never on `title` or `detail`.
- `detail` — the human-readable message for *this* occurrence.
- `instance` — the request path that produced it.
- `error` — **legacy, one release only.** Same string as `detail`. `web/index.template.html:237` and
  `web/pro.template.html:397` read `b.error` to build the message they show; they break to a bare
  "HTTP 404" without it. Drop `error` once both templates read `detail`.

Both handlers set `Content-Type: application/problem+json`. That still matches the `includes("json")`
sniff in `web/index.template.html:237`, so the browser clients keep parsing it as JSON.

## Problem types

| type | status | raised by |
|---|---|---|
| `unauthenticated` | 401 | a route that raises `HTTPException(401, …)` |
| `insufficient_scope` | 403 | an agent key that lacks the scope the route needs |
| `unknown-clinic` | 404 | `app/main.py:168`, `:293`, `:347`, `:533` — clinic_id not in the registry |
| `unknown-posting` | 404 | `app/main.py:186` — posting_id not in the live snapshot |
| `invalid-body` | 400 / 422 | bad JSON, an unknown `mode`/`scope`, `max_credits` out of range, a cron that does not parse |
| `quota-exceeded` | 429 | reserved. Two doors return 429 today, neither through this table — `app/auth.py` on more than 3 magic links per address per hour, or more than 10 wrong `POST /api/auth/login` guesses per source IP per hour — both build their own plain `{"error": "..."}` response, see "Not covered yet" |
| `run-in-progress` | 409 | `app/main.py:338` (a run is already queued or running), `:319` (that run already finished) |
| `adapter-covers-it` | 409 | reserved. Today this is a plan *reason*, not an error: `spend_gate()` returns `{"allowed": false, "reason": "adapter covers it"}` with HTTP 200 (`app/crawl.py:244`) |
| `budget-exceeded` | 409 | `app/main.py:263`, `:354` — the weekly Firecrawl credit budget is spent |

`unknown-<entity>` is a family, not four hand-written cases: any detail that starts with `unknown ` becomes
`unknown-clinic`, `unknown-posting`, `unknown-run`, `unknown-schedule` or `unknown-mechanic`.

## How the type is chosen

The handlers are global. They receive a status code and a detail string and cannot see which route raised,
so `_problem_type()` (`app/main.py:72`) reads the detail text first and falls back to the status code. That
couples the slug to the wording of the `HTTPException(...)` message: **change a detail string and you change
the problem type.** The alternative — a typed exception per problem, raised at every call site — was not
built; the mapping is 12 lines and no route body had to change for it.

## Not covered yet

These paths return `{"error": …}` with `Content-Type: application/json`, because they build their own
`JSONResponse` instead of raising, so no handler sees them:

- the auth middleware, `app/auth.py:399` (401 "owner only" / "sign in required") and `:309` (401 invalid agent key)
- the magic-link routes, `app/auth.py:244`, `:252`, `:270`, `:276`
- `app/stripe_gate.py:216-296`, `:348`, `:387`
- FastAPI's own request validation (`RequestValidationError`), which answers `{"detail": [ … ]}` — a *list*,
  where RFC 9457 wants a string

Converting them is the next step, and `unauthenticated` / `insufficient_scope` above only start appearing
once the auth work lands.

**Open question for Ivan:** `type` points at `/docs/errors.md#…`, which is served by `app/main.py:563` as
`text/markdown` — dereferenceable, but not a stable absolute URI. Should it become an absolute
`https://…/docs/errors.md#…` (RFC 9457 recommends it, and it survives being copied into another agent's logs)?

---

## HANDOFF — for whoever owns `app/main.py`: the CSP blocks the logo of 161 of 407 clinics

*Filed 2026-09-11 by the agent that owns `web/pro.template.html` / `web/deck.template.html`. Not a
problem+json type — it is parked here because this file is the errors page and `app/main.py` is not mine to
edit. Everything below is measured against the real board on a spare port (`127.0.0.1:8599`, a copy of
`data/app.sqlite`), never `?mock=1`.*

**The claim that was wrong.** `app/main.py:70-74` says `img-src 'self' data: https:` was "Verified in
Chromium against `/?mock=1`: with `img-src 'self' data:` every clinic mark fell back to its initials." The
mock board renders **zero** clinic marks, so that run measured a page with nothing to block.

**What the header actually does.** `clinicPhoto()` builds its candidates from the stored
`clinics.website || careers_url` (`web/index.template.html:376`, `web/pro.template.html:664`). In
`data/registry/clinics.csv`: **161 of 407** rows have an `http://` website, 139 `https://`, 107 empty — so
for 161 clinics every candidate URL is `http://` and `img-src … https:` refuses it **before the request
leaves the browser**.

| measured in Chromium | result |
|---|---|
| the 161 `http://` clinics, shipped CSP | **0 painted, 161 initials, 322 CSP console violations** |
| first screen of the real board (`GET /`, 20 rows) | 16 of 20 tiles initials, **41-42** `Loading the image … violates … img-src` lines |
| `/pro#/clinic/16214`, shipped CSP | initials `BA`, **4** violations |
| the same 161 with `img-src … http:` added | 83 painted, 78 initials, 0 violations |
| the same 161 with `upgrade-insecure-requests` added | 0 violations; the requests leave as `https://…` |

The ceiling is 83, not 161: probing all 161 hosts directly (2026-09-11) found 82 that serve an icon over
both schemes, 1 https-only, 1 http-only (`krankenhaus-schwabach.de`) and **77 that serve no icon at either
scheme** — those were always initials and the CSP is not what breaks them. Chromium's ORB also drops the
hosts that answer HTML at a `.png` path (`net::ERR_BLOCKED_BY_ORB`), which is a separate defect from this
one.

**The honest fix: add `upgrade-insecure-requests` to `CSP` in `app/main.py:78-84`.** One token, `img-src`
stays `https:`-only, and it is the root fix rather than a per-page one — it covers
`web/index.template.html` too, which I do not own and which still asks for `http://` favicons on every row
of the public board. Checked, not assumed: it does **not** touch the outbound clinic-website links, which
are top-level cross-origin navigations (`<a href="http://…" target="_blank">` opened as `http://` with the
token in place).

The two alternatives, and why they lose:

- **allow `http:` in `img-src`** — recovers the same 83, but it loosens the policy and the live origin is
  `https://pflege-board.exe.xyz`, where an `http` image is mixed content regardless of what the CSP says.
- **rewrite the 161 stored `http://` website values** — a registry migration that also rewrites the link
  the UI shows people, for 83 icons. If the register says `http://`, the board should keep saying so; that
  is data work with its own decision, not a CSP fix.

**Already done on my side, and it stays even if you take the token:** `clinicPhoto()` in
`web/pro.template.html:664` now asks for every candidate over `https://` (`.map()` on the srcs list). The
stored value is untouched. Before/after on the real board: `/pro#/clinic/16214` went from 4 violations and
the `BA` initials tile to 0 violations and the hospital's own mark. The same two lines in
`web/index.template.html:376` are **not** mine and are still unfixed — the token fixes them, my change does
not.

One more line to correct while you are in there: `tests/test_app_api.py:366-367` repeats the same claim
("Verified in Chromium: `'self' data:` alone blocked every clinic mark on `/`") above its
`assert "img-src 'self' data: https:" in csp`. The assertion itself survives `upgrade-insecure-requests`
untouched — it is a substring match — but the comment is the one that was measured on `?mock=1`.
