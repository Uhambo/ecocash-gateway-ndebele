# Uhambo EcoCash Gateway

A multi-merchant payment gateway for EcoCash. Each merchant connects **their own** EcoCash
merchant account, and money moves directly between EcoCash and that merchant. The gateway never
holds funds. Merchants get one API, safe retries, signed webhooks and a transaction history.

It covers both EcoCash products:

| Product | What it does | Gateway endpoint |
|---|---|---|
| Instant Payments (EIP) | Customer pays merchant by approving a USSD prompt | `POST /v1/charges`, `POST /v1/refunds` |
| Payment Gateway (EPG) | Merchant pays a customer | `POST /v1/payouts/cash-in`, `POST /v1/payouts/change` |

## How it fits together

```
Merchant's app ──API key──▶ Gateway ──merchant's own EcoCash credentials──▶ EcoCash
       ▲                      │  ▲                                             │
       └── signed webhook ────┘  └────────── callback (per-transaction URL) ───┘
```

- **Admin API** (`/admin/...`, admin token): create merchants, store their EcoCash credentials, issue and revoke API keys.
- **Merchant API** (`/v1/...`, merchant API key): charges, refunds, payouts, transaction lookups.
- **Test and live modes**: a key is `uh_test_...` or `uh_live_...`. Test keys use the EcoCash sandbox and test credentials. Live keys are refused until `ECOCASH_EIP_LIVE_URL` / `ECOCASH_EPG_LIVE_URL` are set. Test and live data never mix.

## Security design

| Risk | What the gateway does |
|---|---|
| Database leak exposes merchants' EcoCash credentials | Credentials and webhook secrets are encrypted (Fernet) with a master key kept outside the database. Keys can be rotated: list the new key first in `GATEWAY_MASTER_KEY`. |
| Database leak exposes API keys | Only a keyed hash of each API key is stored. A key is shown once, when created. |
| One merchant reads or refunds another's payments | Every query is filtered by merchant and mode. Other merchants' transactions return 404. |
| A retry or double-click pays twice | Every money request needs an `Idempotency-Key`. A repeat returns the first transaction. Reusing a key for a different request is refused. |
| EcoCash times out mid-payout | The transaction stays `pending` and is settled by asking EcoCash for the outcome. The gateway never re-sends money on its own. |
| Forged "payment succeeded" callback | The callback URL holds an unguessable per-transaction token, and the callback body is ignored: the gateway asks EcoCash for the real status. |
| Merchant cannot trust gateway webhooks | Webhooks are signed (`X-Gateway-Signature: t=<time>,v1=<HMAC-SHA256>`) with the merchant's webhook secret. |
| Webhook URL aimed at internal systems (SSRF) | HTTPS only, no private or reserved addresses, no redirects followed. |
| Stolen key drains a merchant's float | Per-merchant payout limit (default 500.00), per-key rate limit, instant key revocation and merchant suspension. |
| Over-refunding | Refunds are only allowed on succeeded charges and cannot exceed what is left. |
| Bad input | Strict schemas: exact decimal amounts, Econet numbers only, unknown fields rejected. |
| Leaks through errors or logs | Errors return a generic message. The audit log records actions, never secrets. |
| Weak configuration | The app refuses to start with missing or short secrets, or with an http public URL in production. |

## Quick start

> Working on this code? Read [CONTRIBUTING.md](CONTRIBUTING.md) first. It's short, it's rude, it's for your own good.

```bash
pip install -r requirements.txt
```

Then set up your `.env` (see the next section, and actually read it), then start the server:

```bash
python main.py
```

Interactive API docs are at `http://127.0.0.1:8000/docs` (turned off when `GATEWAY_ENV=production`).

## Setting up your `.env` (read this or suffer)

Listen. LISTEN. This is a **payment gateway**. It moves **money**. Real-ish money. The `.env` file
is where every secret that can move that money lives. Treat it like a loaded spoon at a family
braai: carefully, and away from everyone's face.

### Rule zero: `.env` never goes to GitHub

`.env` is in `.gitignore`. It is there **on purpose**. If you remove it from `.gitignore`, a small
bell rings somewhere and a crypto-scam bot gets its wings. Bots scan every public push for secrets
around the clock. They are faster than you. They do not sleep. They have never once been late.

If you EVER push a secret by accident: **rotate it immediately.** Do not "just delete the commit".
Git remembers. The internet remembers. Your ex remembers. Rotate it.

### Step 1: Copy the template

```bash
cp .env.example .env
```

Now open `.env`. Not `.env.example`. **`.env`.** `.env.example` is the menu. `.env` is the meal.
You do not write your PIN on the menu.

### Step 2: Generate the gateway's own secrets

```bash
python -m gateway.keygen
```

It prints three lines. Paste them into `.env`, replacing the empty ones. That's it. That's the step.

- **`GATEWAY_MASTER_KEY`**: encrypts every merchant's EcoCash credentials in the database. Lose it
  and those credentials are unreadable forever, like your handwriting in Form 2. Back it up somewhere
  that is **not** the same disk as the database. Want to rotate it? Put the new key first, comma, old
  key: `GATEWAY_MASTER_KEY=new,old`. Never just delete the old one, unless chaos is your love language.
- **`GATEWAY_KEY_PEPPER`**: mixed into every API key hash. Change it and every merchant's API key
  dies on the spot. Think of it as a "do not touch" sign that is legally binding.
- **`GATEWAY_ADMIN_TOKEN`**: the master password for the admin API. Whoever has this can create
  merchants and issue keys. Do not paste it in WhatsApp. Not even to yourself. **Especially** not
  in the family group.

"But can't I just type my own?" Sure, if it's 32+ random characters. The app checks and **refuses to
start** with anything shorter. `admin123` will not get past the bouncer. Nobody gets past the bouncer.

### Step 3: Your EcoCash sandbox credentials

Get these from the [EcoCash developer portal](https://developers.ecocash.co.zw/dashboard). They are
used by `sandbox_check.py` and the older test scripts. The gateway itself stores each merchant's
credentials encrypted in its database, through the admin API, not in `.env`.

| Variable | Where it comes from | Common way people mess it up |
|---|---|---|
| `ECOCASH_USERNAME`, `ECOCASH_PASSWORD` | Portal → Instant Payments | Decoding the base64 "to be helpful". Don't help. Paste it as-is. |
| `ECOCASH_MERCHANT_CODE`, `_PIN`, `_NUMBER` | Portal → Instant Payments | Putting your **actual bank PIN** in `_PIN`. Why. Why would you. |
| `EPG_CLIENT_ID`, `EPG_CLIENT_SECRET` | Portal → Payment Gateway → Credentials | It says SECRET. In capitals. It's not a suggestion. |
| `EPG_API_USERNAME` | Already filled in: `epg-sandbox-user-v1` | Changing it. It's shared by every sandbox developer. Leave it alone. |
| `EPG_API_PASSWORD_ENCRYPTED` | Payment Gateway playground → **Generate** button | Typing a password here. It wants the scrambled thing the button gives you. Press the button. |
| `EPG_TEST_MSISDN` | **Your** number, after you whitelist it on the Test Numbers page | Leaving `263770000000` from the example. That's nobody's number. EcoCash will say `IC50103` and look at you with pity. |

### Step 4: The boring ones

- `GATEWAY_DATABASE_URL`: SQLite is fine on your laptop. In production use Postgres, because a
  payment gateway running on a single SQLite file is a cry for help.
- `GATEWAY_PUBLIC_BASE_URL`: where EcoCash sends payment callbacks. `localhost` works until you need
  callbacks, at which point EcoCash cannot reach your laptop, however much you believe in it.
  Must be `https://` in production. The app checks. The app always checks.
- `GATEWAY_ENV`: `development` or `production`. Production turns off the API docs and turns on the
  strict checks. Do not set `production` "to see what happens". It will tell you, loudly.
- `ECOCASH_EIP_LIVE_URL`, `ECOCASH_EPG_LIVE_URL`: **leave these empty** until EcoCash gives you
  production access. While they're empty, live API keys are refused. This is a feature, not a bug.
  It's the thing standing between "testing" and "I accidentally paid a stranger in Gweru".

### Step 5: Prove you did it right

```bash
python -m pytest tests -q
```

That needs no credentials at all. If it fails, the problem is the code, not your `.env`. Congratulations,
you're innocent.

```bash
python sandbox_check.py
```

That one talks to the real EcoCash sandbox with your `.env`. `7/7 passed` means you're a genius.
Anything else means you skipped a step above. Go back. We'll wait. We have time. The bots don't.

### Onboard a merchant

```bash
# 1. Create the merchant. The response includes webhook_secret, shown once.
curl -X POST localhost:8000/admin/merchants -H "Authorization: Bearer $GATEWAY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"name": "Corner Shop", "webhook_url": "https://shop.example/hooks/ecocash"}'

# 2. Store their EcoCash sandbox credentials (use .../test/eip for Instant Payments).
curl -X PUT localhost:8000/admin/merchants/<merchant id>/credentials/test/epg -H "Authorization: Bearer $GATEWAY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "...", "client_secret": "...", "api_password_encrypted": "..."}'

# 3. Issue an API key. The response includes api_key, shown once.
curl -X POST localhost:8000/admin/merchants/<merchant id>/api-keys -H "Authorization: Bearer $GATEWAY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"mode": "test", "label": "till 1"}'
```

### Take a payment (as the merchant)

```bash
curl -X POST localhost:8000/v1/charges -H "Authorization: Bearer uh_test_..." \
  -H "Idempotency-Key: order-1001" -H "Content-Type: application/json" \
  -d '{"msisdn": "263771234567", "amount": "10.00", "currency": "USD"}'
```

The response is `202` with `"status": "pending"`. When the customer enters their PIN, the merchant's
webhook receives `transaction.updated`. `GET /v1/transactions/<id>` also returns the latest status.

| Endpoint | Body |
|---|---|
| `POST /v1/charges` | `msisdn`, `amount`, `currency`, `description` |
| `POST /v1/refunds` | `transaction_id`, optional `amount` (default: all that is left) |
| `POST /v1/payouts/cash-in` | `msisdn`, `amount`, `currency`, `beneficiary_reference` |
| `POST /v1/payouts/change` | `msisdn`, `amount`, `currency`, `terminal_id`, `merchant_name` |
| `GET /v1/transactions/{id}` | refreshes from EcoCash if still pending |
| `GET /v1/transactions` | latest first, `?limit=` up to 200 |

Statuses are `pending`, `succeeded` and `failed`. `201` means finished, `202` means still pending.

## Testing

**Offline tests** need no network or credentials. A fake EcoCash stands in for the real one.

```bash
python -m pytest tests -q
```

**Sandbox check** runs the real gateway against the real EcoCash sandbox with the `EPG_*` values in `.env`.
Add `--charge` to also send a USSD prompt to the test number (enter PIN `0000`) and refund it.

```bash
python sandbox_check.py
```

The older single-merchant scripts (`ecocash_client.py`, `epg_client.py`, `test_epg.py`,
`test_all_scenarios.py`) still work and are what EcoCash's production access review was run with.

### Sandbox PINs for charges

`0000` success, `1111` insufficient funds, `2222` incorrect PIN, `9999` limit exceeded.

## Before going live

- Use Postgres (`GATEWAY_DATABASE_URL`) and add Alembic migrations. Tables are currently created at start-up.
- Run behind HTTPS and set `GATEWAY_PUBLIC_BASE_URL` to the public address so EcoCash callbacks arrive.
- Keep `GATEWAY_MASTER_KEY` in a secrets manager, not on the same disk as the database.
- Move rate limiting to Redis if you run more than one server.
- Merchant webhooks are sent once. Add retries with back-off (deliveries are already recorded).
- Add a background job that settles `pending` transactions, so nothing depends on callbacks or polling.
- Merchants have no self-service sign-up or dashboard yet. Onboarding is through the admin API.
- Confirm with EcoCash that each merchant's production access covers use through a third-party platform.
