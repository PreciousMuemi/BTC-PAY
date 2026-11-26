# TembeaPay

TembeaPay accepts tourist payments in Bitcoin Lightning and pays merchants in
KES. Configure payout and Lightning providers through `.env`; credentials
should remain server-side.

## Environment variables

| Variable | Description |
| --- | --- |
| `AT_USERNAME`, `AT_API_KEY` | Africa's Talking credentials for SMS and airtime |
| `LIGHTNING_PROVIDER` | `mock` (default) or `blink` |
| `BLINK_API_URL` | Blink GraphQL endpoint; defaults to `https://api.blink.sv/graphql` |
| `BLINK_API_KEY` | Server-side Blink API key; required when provider is `blink` |
| `BLINK_WALLET_ID` | Optional Blink BTC wallet ID; otherwise detected from the account |
| `PAYOUT_MODE` | `simulated` (default), `daraja_sandbox`, or `daraja_live` |
| `DARAJA_ENV` | `sandbox` or `production`/`live`, matching the selected Daraja payout mode |
| `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET` | Daraja OAuth credentials |
| `DARAJA_INITIATOR_NAME`, `DARAJA_SECURITY_CREDENTIAL` | Daraja B2C initiator credentials |
| `DARAJA_SHORTCODE` | Daraja B2C shortcode |
| `PUBLIC_BASE_URL` | Public HTTPS base URL for Daraja result and timeout callbacks |
| `MPESA_CALLBACK_TOKEN` | Secret path token used to protect Daraja callbacks |
| `WEBHOOK_SECRET` | Secret required by the Lightning paid webhook |
| `MERCHANT_NAME` | Merchant display name |
| `MERCHANT_MPESA_TARGET` | Merchant's M-Pesa payout phone number |
| `MERCHANT_PHONE` | Phone number for merchant receipt SMS |
| `AIRTIME_REWARD_KES` | Tourist airtime reward in whole KES (default `20`) |
| `QUOTE_TTL_MINUTES` | Quote and invoice lifetime in minutes (default `15`) |
| `KES_PER_BTC_FALLBACK` | Required fallback conversion rate in KES per BTC |

Copy `.env.example` to `.env` and set credentials locally. Do not commit `.env`.
Use `PAYOUT_MODE=simulated` and `LIGHTNING_PROVIDER=mock` for local development.

Run the API with `uvicorn app.main:app --reload` and tests with `pytest`.
