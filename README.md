# Stella — Anarchy Community Bot

Stella is an independent Python bot for Anarchy Apps. It has its own codebase, configuration, storage and deployment. It is not a renamed copy of Katheryne.

## Included in this first implementation

- Signed Anarchy webhook receiver with timestamp validation, HMAC-SHA256 verification, payload limits and in-memory event de-duplication.
- Anarchy API client for sending messages.
- Commands: `!ayuda`, `!ping`, `!miuid`, `!azar`.
- Moderation records: `!advertir`, `!advertencias`, `!quitaradvertencia`.
- Per-room anti-spam toggle: `!antispam activar|desactivar`.
- Exact UID-based administrator permissions.
- SQLite persistence for moderation records and room configuration only.

## Privacy and retention

Stella does not save message contents, conversation histories, personal profiles, or long-term user memory. Anti-spam fingerprints and counters exist only in process memory. Moderation records are operational data and are automatically deleted after 90 days. Room configuration remains until an administrator changes it or the database is removed.

The Anarchy API does not expose a documented ability for a bot to delete or mute another member's messages. A warning is therefore an internal moderation record, not a platform-enforced punishment.

## Configuration

Set these environment variables in the hosting provider:

| Variable | Required | Purpose |
|---|---:|---|
| `ANARCHY_BOT_TOKEN` | Yes | Bot API token |
| `ANARCHY_WEBHOOK_SECRET` | Yes | HMAC webhook secret |
| `ADMIN_UIDS` | No | Comma/semicolon/newline-separated administrator UIDs |
| `COMMAND_PREFIX` | No | Command prefix; defaults to `!` |
| `DATA_DIR` | No | SQLite directory; defaults to `./data` |

Do not commit real credentials. See [`.env.example`](.env.example).

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn main:app --reload
```

The app exposes `GET /health` and receives Anarchy webhooks at `POST /webhook` (also `POST /`).

## Render

The included Blueprint targets the existing `stella-anarchy` Free web service in Ohio. Configure the required environment variables in Render before connecting the Anarchy webhook to `https://YOUR-SERVICE.onrender.com/webhook`.

Free Render services can sleep when idle. A sleeping service may take time to wake for a webhook; use a paid always-on plan if the bot later requires uninterrupted response times.

## Development

Run tests with:

```bash
pip install -r requirements-dev.txt
pytest -q
```

Stella is intentionally a clean foundation. More community modules should be added independently and tested before enabling them in production.
