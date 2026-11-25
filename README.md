# Stella — Anarchy Community Bot

Stella is an independent Python bot for Anarchy Apps. It has its own codebase, configuration, storage and deployment. It is not a renamed copy of Katheryne and does not include Genshin Impact commands or integrations.

## Current feature set

- Signed Anarchy webhook receiver with timestamp validation, HMAC-SHA256 verification, streamed payload limits, 15-minute in-memory event de-duplication and bounded caches.
- Anarchy API client for sending chat messages and replies. At startup, Stella calls `GET /me` and logs its authenticated `botId` and account `uid`; this works before Stella joins any chat.
- General commands: `!ayuda`, `!ping`, `!miuid`, `!azar`, `!frase`.
- Moderation records: `!advertir`, `!advertencias`, `!quitaradvertencia`.
- Room anti-spam configuration: `!antispam activar|desactivar`.
- Polls with voting, vote changes, results and closure: `!encuesta crear|votar|resultados|cerrar|lista`.
- Community events with date/time, location, RSVPs and closure: `!evento crear|lista|apuntarse|cerrar`.
- Aggregate activity reports for administrators: `!actividad`.
- Exact-match automatic replies, configured by administrators: `!respuesta añadir|quitar|lista`.
- Preliminary link inspection: `!analizar`.
- Gemini-first AI with Groq fallback, using the same models as Katheryne: Gemini `gemini-3.5-flash-lite` and Groq `openai/gpt-oss-20b`. Commands: `!ia`, `!ia estado`, `!traducir`, `!investigar`.
- LiveKit voice-room connection for active Anarchy calls: `!join`, `!salirsala`, `!voz`.
- Text-to-speech with Edge TTS, Spanish voices, speed and volume controls: `!leer`, `!noleer`, `!tts voces`, `!tts voz`, `!tts velocidad`, `!tts volumen`.
- Music DJ with open-license music catalogues (Internet Archive and Jamendo where configured), exact result selection, bounded queues, pause/resume/skip/stop and volume: `!dj buscar`, `!dj reproducir`, `!dj encolar`, `!dj cola`, `!dj pausa`, `!dj reanudar`, `!dj saltar`, `!dj detener`.
- Music is not downloaded from YouTube or arbitrary URLs. Tracks must come from the supported catalogues and pass source/license validation.
- Exact UID-based administrator permissions.

## Privacy, data and retention

Stella does not save message contents, conversation histories, personal profiles or long-term user memory. Prompts sent to the optional AI provider are processed for that request and are not written to Stella's database; the provider's own retention policy still applies.

Only these operational records are stored in SQLite:
- Moderation warnings: 90 days.
- Aggregate per-user daily message counts: 7 days.
- Polls and their voter identifiers: 30 days after poll creation.
- Events and attendee identifiers: 30 days after the event date.
- Room settings, polls/event definitions while current, and automatic-reply rules: retained as community configuration until deleted or the database is removed.

Anti-spam fingerprints, recent command state, webhook de-duplication and cooldowns remain in RAM and are bounded/expired. SQLite cleanup runs at startup and at least hourly while the process receives events.

The Anarchy API's bot messaging capabilities do not mean the bot can delete or mute another member's messages. A warning is an internal moderation record, not a platform-enforced punishment. Link inspection is heuristic and does not certify that a destination is safe.

## Configuration

Set these variables in the hosting provider. Never commit real credentials.

| Variable | Required | Purpose |
|---|---:|---|
| `ANARCHY_BOT_TOKEN` | Yes | Anarchy bot API token |
| `ANARCHY_WEBHOOK_SECRET` | Yes | HMAC webhook signing secret |
| `ADMIN_UIDS` | No | Administrator UIDs separated by comma, semicolon or newline |
| `COMMAND_PREFIX` | No | Command prefix; defaults to `!` |
| `ANARCHY_API_BASE` | No | Anarchy API base URL |
| `DATA_DIR` | No | SQLite directory; defaults to `./data` |
| `GEMINI_API_KEY` | No | Gemini primary provider key |
| `GEMINI_MODEL` | No | Defaults to `gemini-3.5-flash-lite` |
| `GEMINI_API_URL` | No | Gemini API base URL |
| `GROQ_API_KEY` | No | Groq fallback provider key |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b` |
| `GROQ_API_URL` | No | Groq Responses API base URL |
| `STELLA_AI_PRIMARY_PROVIDER` | No | `gemini` (default) or `groq` |
| `ANARCHY_COMMUNITY_BOT_TOKEN` | No | Optional separate token for LiveKit voice endpoints; defaults to the bot token |
| `JAMENDO_CLIENT_ID` | No | Optional Jamendo catalogue access |
| `TTS_VOICE` | No | Defaults to Chilean Spanish Catalina |

AI uses Gemini first and automatically tries Groq if Gemini fails. Both provider keys are optional, but at least one is required for `!ia` and `!traducir`. The AI provider response is not saved in Stella's SQLite database and Stella does not maintain conversation history. The external providers' own processing and retention policies still apply. Web search uses DuckDuckGo's HTML search endpoint and may be unavailable if the provider blocks the request.

Voice commands require an active Anarchy call and working LiveKit credentials from the Anarchy voice API. TTS additionally requires Edge TTS, PyAV and NumPy. Music playback uses only validated tracks from the supported open-license catalogues; the optional Jamendo source requires `JAMENDO_CLIENT_ID`. Voice rooms, TTS queues, music queues, search selections and downloaded audio are temporary and are removed on room exit or process shutdown.

See [`.env.example`](.env.example) for safe empty placeholders.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# Put credentials in your local environment; do not commit .env.
uvicorn main:app --reload
```

The app exposes `GET /health` and receives Anarchy webhooks at `POST /webhook` (also `POST /`).

## Connect Anarchy

Set the bot's webhook URL to:

```
https://YOUR-SERVICE.onrender.com/webhook
```

Use the matching webhook secret configured for the bot in Anarchy. Stella validates `X-Anarchy-Bot-Timestamp` and `X-Anarchy-Bot-Signature` using HMAC-SHA256 over `timestamp + "." + raw_body`.

## Render deployment

The included `render.yaml` targets the `stella-anarchy` Free web service in Ohio. Free Render web services can sleep when idle and have ephemeral local filesystems; SQLite data can be lost during restarts or deploys. This build intentionally avoids requiring paid persistent storage. If durable moderation/configuration data is required later, move the store to a managed PostgreSQL database.

## Development

Install and run:

```bash
pip install -r requirements-dev.txt
pytest -q
```

GitHub Actions runs the same test suite on pushes and pull requests.
