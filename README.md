# Swiggy Telegram Bot — Render Web Service Bundle

## Deploy on Render

1. Upload this folder to a GitHub repository.
2. In Render, choose **New + → Blueprint** and select the repository (or create a Web Service manually).
3. Set environment variables:
   - `BOT_TOKEN`: create/retrieve the Telegram bot token using BotFather.
   - `ADMIN_IDS`: comma-separated numeric Telegram user IDs, e.g. `123456789`.
4. Deploy. Render will install `requirements.txt` and run `python swiggy_bot.py`.
5. Open your Render service URL. `/` and `/healthz` return JSON health status.

## Important Render free-tier behavior

This is a real HTTP health endpoint and binds to Render's `$PORT`. It is **not** a keep-alive workaround: Render free web services may spin down after inactivity, and a health endpoint does not guarantee a continuously running Telegram polling process. Do not use external pingers to bypass plan limits. For reliable always-on bot polling, use a supported always-on/background worker plan or a host that permits persistent processes.

## Configuration and safety

- Never commit `BOT_TOKEN` or other secrets. Configure them in Render Environment.
- Admin controls are protected by `ADMIN_IDS`.
- This deployment scaffold intentionally disables in-chat OTP capture, session-token ingestion/export, automated bulk referral processing, and automated offer claiming. Those workflows can expose account credentials or automate reward claims. API endpoint constants and parsing helpers remain in the source for reference, but these sensitive workflows are not deployed.
- `bot_stats.json` is local filesystem state. Render free instances may have ephemeral storage; use a persistent store if you need durable user statistics.
- Python syntax compilation was checked locally. Live Telegram/Render behavior still needs testing.


## Telegram keyboard compatibility fix

Reply-keyboard buttons use only standard Telegram Bot API fields. Telegram's `KeyboardButton.style` does not accept dictionaries, so the old styling values were removed. A global error handler is registered to log update errors and provide a friendly message.
