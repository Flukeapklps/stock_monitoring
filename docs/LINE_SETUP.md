# LINE Setup

The platform uses the **LINE Messaging API** with a LINE Official Account. (LINE Notify is discontinued and not used.)
Docs: https://developers.line.biz/en/docs/messaging-api/ · Reference: https://developers.line.biz/en/reference/messaging-api/

## 1. Create the channel

1. Create a LINE Official Account and enable the Messaging API for it (LINE Official Account Manager →
   Settings → Messaging API), choosing or creating a provider in the LINE Developers Console.
2. In the LINE Developers Console → your channel:
   - **Basic settings → Channel secret** → `LINE_CHANNEL_SECRET`
   - **Messaging API → Channel access token (long-lived)** → issue → `LINE_CHANNEL_ACCESS_TOKEN`
3. In LINE Official Account Manager → Response settings: turn **Webhooks ON**, and turn auto-reply / greeting
   messages off (the bot sends its own welcome and replies).

## 2. Configure the webhook

The webhook must be public HTTPS: `https://<your-domain>/api/v1/line/webhook` (set it as `LINE_WEBHOOK_URL` too).
In the Developers Console → Messaging API → Webhook URL, paste it, enable **Use webhook**, and click **Verify**
(the platform answers 200 to the empty verification payload).

Every request is verified with HMAC-SHA256 of the **raw body** using the channel secret (`X-Line-Signature`,
constant-time comparison); invalid signatures get 401. Events are stored (deduplicated by `webhookEventId`) and
processed after the 200 response.

## 3. Environment

```
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_CHANNEL_SECRET=...
LINE_USER_ID=                 # your own user ID (see step 4); receives admin test messages
LINE_WEBHOOK_URL=https://stocks.example.com/api/v1/line/webhook
LINE_ENABLED=true
LINE_DEFAULT_NOTIFICATION_ENABLED=true
LINE_ALERT_COOLDOWN_MINUTES=1440
LINE_MAX_RETRY_ATTEMPTS=3
PUBLIC_BASE_URL=https://stocks.example.com   # HTTPS links for card buttons
```

Restart: `docker compose up -d backend worker`. Tokens stay on the server; the dashboard shows only `••••last4`.

## 4. Register recipients

A user must **add the Official Account as a friend** before they can receive push messages. On the follow event the
platform registers their LINE user ID, creates their notification profile (defaults below) and sends a welcome
message. To get your own `LINE_USER_ID`, follow the account, then open **Dashboard → LINE Integration →
Recipient** (admin) — or `GET /api/v1/line/users` — and copy the ID.

To share watchlists/portfolios between the web account and LINE: Dashboard → LINE Integration → **Link my LINE
account**, then send `/link CODE` to the bot.

Default notifications: daily, weekly and monthly reports, strong bullish, accumulation, trend weakening,
fundamental and portfolio alerts **on**; price alerts **off**. Change them on the dashboard or with
`/notify <type> on|off`.

## Bot commands (case-insensitive)

`/help` `/market` `/watchlist` `/stock AAPL` `/signal AAPL` `/score AAPL` `/watch AAPL` `/unwatch AAPL`
`/portfolio` `/top` `/settings` `/notify <daily|weekly|monthly|bullish|accumulation|weakening|price|fundamental|portfolio> on|off`
`/test` `/link CODE`. Commands use reply messages (reply tokens are single use and never retried); scheduled alerts
use push messages.

## Delivery semantics

- Statuses: `QUEUED → SENDING → ACCEPTED | FAILED | UNKNOWN`, or `SKIPPED` (unfollowed, 24h cap).
  **ACCEPTED means the LINE API accepted the request — not that the user read it.**
- Each notification has a fixed `X-Line-Retry-Key`; retries reuse it, and a 409 duplicate response counts as
  accepted. Retries (5xx, 429 rate limit, network): 30 s, 60 s, 120 s… up to `LINE_MAX_RETRY_ATTEMPTS`.
  A timeout on the final attempt is `UNKNOWN`. 400/401/403 and monthly quota exhaustion are permanent `FAILED`.
- Duplicate alerts are blocked by a per-recipient dedupe key; a per-symbol/category cooldown, a 24-hour cap,
  quiet hours (deferred, not dropped) and the user's minimum score / RSI zone apply.
- Quota and consumption (`/v2/bot/message/quota[/consumption]`) are checked (cached 10 min) before pushing.
- Every Flex message is validated locally against the Messaging API schema before queueing; the admin test also
  calls LINE's `/v2/bot/message/validate/push`.

## Manual end-to-end test with a real Official Account

1. Configure steps 1–3; deploy with HTTPS. Dashboard → LINE Integration → **Test connection** shows the bot name
   (401 means an invalid access token).
2. Webhook **Verify** in the Developers Console succeeds; the LINE page shows *Webhook: RECEIVING* after the next event.
3. Add the account as a friend from a phone → welcome message arrives; the user appears under Recipient.
4. Send `/help`, `/market`, `/watch AAPL`, `/stock AAPL`, `/score AAPL`, `/watchlist` → replies arrive; check
   that Flex cards render (dark card, price, score, trend badge, analysis bullets, *View Stock Details* button).
5. Set `LINE_USER_ID`, click **Send test message** → *ACCEPTED* in Notification History and a message on the phone.
6. **Send as test alert** for a symbol and **Send daily/weekly/monthly report** → cards arrive.
7. Turn *Accumulation alerts* off, then `/settings` in LINE shows it OFF; `/notify accumulation on` re-enables it.
8. Add a price rule close to the current price, enable *Price alerts*, run **Data Health → daily_update → Run now**
   after the next session → alert arrives once (repeat runs do not resend).
9. Block/unfollow the account → user shows *unfollowed*; queued messages become `SKIPPED`.
10. Send a request with a wrong signature (e.g. `curl -X POST https://<domain>/api/v1/line/webhook -d '{}' -H 'x-line-signature: x'`) → 401.
