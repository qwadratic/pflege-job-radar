# Stripe – pay per closed job posting

**Status today: not configured.** No `STRIPE_SECRET_KEY` on the VM, the Stripe integration is not attached. Everything
in `app/stripe_gate.py` is feature-flagged on that key: `GET /api/stripe/status` says `configured: false`, checkout /
webhook / usage answer `503 {"error": "stripe not configured"}`, and the gate in `/pro` resolves to owner / anonymous
(see `docs/auth.md`). The one thing that works already is the local ledger of closed postings – so the numbers are right
the day Stripe is switched on.

## The model

- A **customer** (a clinic or a recruiter) subscribes to **one metered price** (`STRIPE_PRICE_ID`). Unit = one closed
  posting. No base fee, nothing up front: the monthly invoice is `closed postings × unit price`.
- A posting is **closed** when the customer marks it as filled – *Stelle besetzt* – in `/pro`:
  `POST /api/postings/{id}/closed`. That call writes one row in `closed_postings` and, when Stripe is configured and
  the closer maps to a Stripe customer, **one usage unit** on the customer's subscription.
- **Idempotent per posting**: a second click answers the stored row (`already: true`), never a second unit.
  The Stripe side is idempotent too (`Idempotency-Key: closed-posting-<id>`, meter-event `identifier`).

### What is NOT billed

- A posting that merely **expires** or disappears from the hospital board – it is not attributable to the customer.
- Postings closed by the **owner** without `customer_email` (housekeeping): ledger row, no usage.
- Postings closed by a **cancelled** customer (`customers.status != 'active'`): ledger row, no usage.
- Postings closed while Stripe is **not configured**: ledger row with `stripe_error = 'stripe not configured'`; not
  back-filled automatically – decide by hand from the ledger when Stripe goes live.
- Anything a customer does *besides* closing: browsing, searching, exporting, refetches – free.

## Endpoints (`app/stripe_gate.py`, mounted at `/api`)

| method / path | who | does |
|---|---|---|
| `GET /api/stripe/status` | everyone | `{configured, price_id_set, webhook_set, meter, customers, closed_this_period, period_start}` – `customers` = active rows, `closed_this_period` = ledger rows since the 1st of the current UTC month (Stripe's own invoice period starts on the subscription anchor day; the dashboard is authoritative) |
| `POST /api/stripe/checkout {email}` | everyone | creates a Checkout Session (`mode=subscription`, one line item = the metered price, `customer_email`, `success_url=/pro?stripe=ok`, `cancel_url=/pro?stripe=cancel`) and answers `{url, id}`. If the caller carries a `pj_session` cookie its SHA-256 sid goes along as `metadata[sid_hash]` (never the sid) so the webhook can turn that session into a customer session. `400` no e-mail, `503` no key / no price, `502` Stripe error (message only, never the key) |
| `POST /api/stripe/webhook` | Stripe | body verified with `STRIPE_WEBHOOK_SECRET` (below). `checkout.session.completed` → `customers(email, stripe_customer_id, status='active')` upsert (+ the session named in `metadata.sid_hash` becomes `role='customer'`; owner sessions keep their role). `customer.subscription.deleted` → `status='cancelled'`. Other events: `200 {handled: false}`. `400` bad signature / not JSON, `503` no key / no webhook secret |
| `POST /api/postings/{id}/closed` | owner or customer | the billable event. Body optional, owner only: `{customer_email}` attributes the close to that customer. Answers the ledger row + `{already, billed}`. `401` anonymous, `400` id ≤ 0 |
| `GET /api/postings/{id}/closed` | owner or customer | `{posting_id, closed, ...row}` |

Usage record, two flavours (picked by env):

- **Billing Meter** (`STRIPE_METER_EVENT_NAME` set – the way new Stripe accounts meter): `POST /v1/billing/meter_events`
  `{event_name, identifier: closed-posting-<id>, payload[stripe_customer_id], payload[value]=1, timestamp}`.
- **Legacy usage records** (env unset): `GET /v1/subscriptions?customer=…&status=active`, pick the item whose
  `price.id == STRIPE_PRICE_ID`, `POST /v1/subscription_items/{si}/usage_records {quantity: 1, action: increment}`.

Both go through plain `requests` against `STRIPE_API_BASE` with `Authorization: Bearer STRIPE_SECRET_KEY`. The key is
never logged or echoed; Stripe error messages are truncated to 300 chars and passed through.

### Webhook signature (Stripe v1 scheme)

Header `Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>]`. `hex = HMAC-SHA256(STRIPE_WEBHOOK_SECRET, "<t>.<raw body>")`.
Any `v1` may match (key rotation); `|now − t| ≤ 300 s`. `stripe_gate.verify_signature()` / `sign_payload()` implement
and produce it (the latter for tests and the curl walkthrough below).

## Tables (SQLite `data/app.sqlite`)

- `closed_postings(posting_id primary key, by_email, at, stripe_usage_id, stripe_customer_id, stripe_error)` – the ledger.
  `stripe_usage_id` = usage record id or meter-event identifier; `stripe_error` = why no unit was written.
- `customers(email primary key, stripe_customer_id, status, created_at)` and `sessions(…, stripe_customer_id)` – schema in
  `app/runs.py` (auth track); `stripe_gate.SCHEMA` repeats the same `create table if not exists` so either module may
  boot first.

## Env vars

| var | default | meaning |
|---|---|---|
| `STRIPE_SECRET_KEY` | unset | the feature flag. `sk_test_…` for test mode, `sk_live_…` for real money |
| `STRIPE_PRICE_ID` | unset | the metered price (`price_…`). Checkout answers 503 without it |
| `STRIPE_WEBHOOK_SECRET` | unset | `whsec_…` of the webhook endpoint. Webhook answers 503 without it |
| `STRIPE_METER_EVENT_NAME` | unset | set = usage goes to Billing Meter events under this name; unset = legacy usage records |
| `STRIPE_API_BASE` | `https://api.stripe.com` | override for a proxy / the exe.dev Stripe integration host |
| `PUBLIC_BASE_URL` | `https://pflege-board.exe.xyz` | host in `success_url` / `cancel_url` |

Put them in `/home/exedev/repo/.env` (the systemd unit loads it) and restart `pflege-web`.

## Creating the metered price in the Stripe dashboard

1. **Product catalog → + Add product.** Name *Besetzte Stelle* (closed job posting). Description: *Eine Einheit pro
   Stellenanzeige, die der Kunde als besetzt markiert.*
2. **Pricing model: Usage-based** (in older dashboards: *Standard pricing* → *Usage is metered*).
   - New accounts: pick or create a **Meter** – event name e.g. `closed_posting`, aggregation **Sum**, then put
     `STRIPE_METER_EVENT_NAME=closed_posting`.
   - Accounts still on legacy metered prices: *Charge for metered usage by* **Sum of usage values during period**;
     leave `STRIPE_METER_EVENT_NAME` unset.
   - Billing period **Monthly**, price e.g. **EUR 49,00 per unit**, *Per unit* (no tiers). Tax behaviour as needed.
3. Save; copy the **Price ID** (`price_…`) → `STRIPE_PRICE_ID`.
4. **Developers → API keys** → secret key → `STRIPE_SECRET_KEY`.
5. **Developers → Webhooks → + Add endpoint.** URL `https://pflege-board.exe.xyz/api/stripe/webhook`, events
   `checkout.session.completed` and `customer.subscription.deleted`. Copy the **Signing secret** (`whsec_…`) →
   `STRIPE_WEBHOOK_SECRET`.
6. Optional: **Settings → Customer portal** so customers can cancel themselves (the cancellation arrives as
   `customer.subscription.deleted`).

## Test-mode walkthrough

Test mode = the toggle in the dashboard; every ID above has a `_test_` twin. Nothing is charged.

```sh
# 1. env (test keys), restart the web service
STRIPE_SECRET_KEY=sk_test_…  STRIPE_PRICE_ID=price_…  STRIPE_WEBHOOK_SECRET=whsec_…  [STRIPE_METER_EVENT_NAME=closed_posting]
curl -s https://pflege-board.exe.xyz/api/stripe/status
# {"configured": true, "price_id_set": true, "webhook_set": true, "customers": 0, "closed_this_period": 0, ...}

# 2. checkout
curl -s -X POST https://pflege-board.exe.xyz/api/stripe/checkout -H 'content-type: application/json' \
     -d '{"email":"clinic@example.org"}'
# {"url": "https://checkout.stripe.com/c/pay/cs_test_…", "id": "cs_test_…"}
# open the url, pay with 4242 4242 4242 4242, any future date, any CVC -> lands on /pro?stripe=ok

# 3. the webhook fires checkout.session.completed -> customers row (status active)
curl -s https://pflege-board.exe.xyz/api/stripe/status      # customers: 1
# the customer now gets a magic link: POST /api/auth/magic {"email":"clinic@example.org"} (docs/auth.md)

# 4. close a posting as that customer (cookie from the magic link) -> one usage unit
curl -s -X POST https://pflege-board.exe.xyz/api/postings/4711/closed -b 'pj_session=…'
# {"posting_id": 4711, "by_email": "clinic@example.org", "billed": true, "stripe_usage_id": "…", "already": false, ...}
curl -s -X POST https://pflege-board.exe.xyz/api/postings/4711/closed -b 'pj_session=…'   # already: true, no 2nd unit

# 5. dashboard -> Customers -> the subscription -> Usage: 1. The draft invoice shows 1 × unit price.
# 6. cancel the subscription in the dashboard -> customer.subscription.deleted -> status cancelled, no more magic links.
```

Replaying a webhook locally without Stripe (`sign_payload` is the v1 scheme):

```sh
cd /home/exedev/repo && set -a && . ./.env && set +a
BODY='{"id":"evt_1","type":"checkout.session.completed","data":{"object":{"customer":"cus_test","customer_details":{"email":"clinic@example.org"}}}}'
SIG=$(.venv/bin/python -c "import sys,os; from app import stripe_gate as SG; print(SG.sign_payload(sys.argv[1].encode(), os.environ['STRIPE_WEBHOOK_SECRET']))" "$BODY")
curl -s -X POST http://127.0.0.1:8501/api/stripe/webhook -H "Stripe-Signature: $SIG" -H 'content-type: application/json' -d "$BODY"
```

## Going live

Swap the three `_test_` values for live ones, add the live webhook endpoint (its own `whsec_`), restart. Nothing else
changes. Back-fill: `select * from closed_postings where stripe_usage_id is null` lists closes that never reached
Stripe (not configured / Stripe down / no subscription) – bill by hand or ignore, they are never retried automatically.
