# Stella — Anarchy Community Bot

Stella is an independent Python bot for Anarchy Apps. It has its own codebase, configuration, storage and deployment. Its modules are built around general community, AI, research, moderation, profile, entertainment, music and voice features.

## Current feature set

- Signed Anarchy webhook receiver with timestamp validation, HMAC-SHA256 verification, streamed payload limits, 15-minute in-memory event de-duplication and bounded caches.
- Anarchy API client for sending chat messages and replies. At startup, Stella calls `GET /me` and logs its authenticated `botId` and account `uid`; this works before Stella joins any chat.
- General commands: `!ayuda`, `!ping`, `!miuid`, `!azar`, `!frase`.
- General utilities: safe mathematical evaluation with `!calcular` (`!calc`, `!matematica`), date/time and IANA time zones with `!fecha` and `!zona`, spelling suggestions with `!ortografia`, link inspection with `!analizar`, and AI-assisted text operations with `!analizartexto resumir|corregir|reescribir|extraer|contradicciones` (`!texto`).
- Community entertainment added: `!trivia` / `!responder`, `!adivina` / `!intento`, `!verdad`, `!8ball`, `!elige`, `!chiste`, `!reto`, `!ship`, `!roast`, and text-based social actions such as `!abrazo`, `!beso`, `!saludar`, `!consolar`, and `!ayudar`. Active game rounds are volatile and expire; they are not written to SQLite.
- `!ayuda` and `!menu` expose the full command catalogue with category filters: `ia`, `social`, `juegos`, `musica`, `multimedia`, and `admin`. Stella can also be addressed by name for supported natural-language commands.

- Moderation records: `!advertir`, `!advertencias`, `!quitaradvertencia`.
- Room anti-spam configuration: `!antispam activar|desactivar`.
- Polls with voting, vote changes, results and closure: `!encuesta crear|votar|resultados|cerrar|lista`.
- Community events with date/time, location, RSVPs and closure: `!evento crear|lista|apuntarse|cerrar`.
- Aggregate activity reports for administrators: `!actividad`.
- Exact-match automatic replies, configured by administrators: `!respuesta añadir|quitar|lista`.
- Preliminary link inspection: `!analizar`, with domain, redirect-format, suspicious-TLD and adult-content signals. It is heuristic, not an antivirus verdict.
- Multimedia search: `!multimedia imagen|video|audio`, `!imagen`, `!video`, and `!audio` using Wikimedia Commons and Internet Archive. Image results include license attribution where available.
- Advanced AI command router with Gemini-first/Groq fallback, provider pacing, retry handling, structured tool calls, command permissions, safe multi-step plans, temporary context and research synthesis. Multilingual insult guidance distinguishes direct attacks on Stella from quotations, language questions and insults aimed at others. Commands: `!ia`, `!ia ayuda`, `!ia estado`, `!ia olvidar`, `!ia guardian`, `!ia diagnostico`, `!ia resumir`, `!traducir`, `!investigar` with general, comparison, verification, chronology and report modes. AI context is limited to two recent exchanges per room/user, expires after five minutes and can be cleared immediately.
- LiveKit voice-room connection for active Anarchy calls: `!join`, `!salirsala`, `!salas`, and `!voz participantes|hablando|estado`. Participant state is volatile and expires.
- Per-user, per-room music playlists and favorites: `!playlist crear|lista|agregar|ver|reproducir|borrar` and `!dj favorito|favoritos`. Playlists only save results from the supported music catalogues.
- Text-to-speech with Edge TTS, Spanish voices, speed and volume controls: `!leer`, `!noleer`, `!tts voces`, `!tts voz`, `!tts velocidad`, `!tts volumen`.
- Music DJ with open-license music catalogues (Internet Archive, Wikimedia Commons, Openverse, ccMixter, Audius and Jamendo where configured), exact result selection, bounded queues, pause/resume/skip/stop and volume: `!dj buscar`, `!dj reproducir`, `!dj encolar`, `!dj cola`, `!dj pausa`, `!dj reanudar`, `!dj saltar`, `!dj detener`; shortcuts `!pausa`, `!reanudar`, `!saltar`, `!cola`, `!djstop` also route to the same controller.
- YouTube is available as a separate external discovery action: `!dj youtube <tema o enlace>` opens the official YouTube search/video page. It is not mixed with the open-license catalogues and is never added to the LiveKit queue.
- Music is not downloaded from YouTube or arbitrary URLs. LiveKit tracks must come from the supported open-license catalogues and pass source/license validation. DJ controls include repeat, queue editing, ambience search, favorites, and source diagnostics.
- Profiles and social economy: `!perfil`, `!bio`, `!miestado`, `!puntos`, `!ranking` (`!top`), `!diario` (`!daily`), `!trabajar`, `!pagar` (`!transferir`), `!regalo`, `!muro`, `!afinidad` (`!amistad`, `!reputacion`) and `!duelo`.
- High-confidence command typo suggestions; suggestions are never executed automatically.
- User references: reply to a member's message with `!mencionar [texto]` to put their visible `@nickname` in Stella's reply. The documented Anarchy bot-send API does not currently provide an outbound mention/notification field, so this is visible text and not a guaranteed notification.
- Commands are also parsed when the user's reply begins with an explicit command after a leading @-mention of the replied-to member. Render logs each text message delivered by Anarchy, including edits and Stella's successful outgoing messages; recognizable credentials are redacted. A community bot needs the Agent's read-all-messages permission to receive ordinary chat text.
- Reversible global chat silence, controlled only by the exact owner UID in `SINNER_UID`: say "No le hables a nadie" or use `!silenciarchat`; restore with "Ya puedes hablar", `!hablarchat` or `!despertar`. While silenced, Stella ignores messages, commands and voice reading; pending TTS is cancelled. The wake-up path remains available to the owner.
- Exact UID-based administrator permissions. Admin aliases include `!admin`, `!centro`, `!nexus`, `!paneladmin`, `!sistema`, `!diagnostico`, `!rendimiento`, `!laboratorio`, `!fuentes`, `!saludfuentes`, `!metricas` and `!autotest`. Admins can update Stella's name/status with `!perfilbot` and set its avatar with `!avatarbot`; avatar uploads are restricted to direct HTTPS images from Wikimedia Commons, Imgur, Unsplash or Pexels, validated for real image format, dimensions and a 512 KB cap.
- `/metrics` exposes aggregate Prometheus counters and can be protected with `PROMETHEUS_METRICS_TOKEN`; `/health` reports privacy-safe component readiness and uptime, while `/ready` checks the required Anarchy credentials and SQLite before traffic is accepted.

## Privacy, data and retention

Stella can optionally index ordinary room messages in Qdrant when configured. Indexed text expires after 30 days by default, cleanup runs every six hours, and retrieval is restricted to the current room and the requesting user. Users can enable or disable this with `!memoria activar` / `!memoria desactivar`, search their own indexed content with `!memoria buscar <consulta>`, or delete it with `!memoria olvidar`. AI conversation continuity has a separate explicit opt-in: `!memoria conversacion activar`. When enabled, Stella stores at most ten recent user/assistant turns plus up to twenty durable facts in a dedicated Qdrant collection, scoped to the room and user, with a 30-day default expiry. Durable facts are captured from clear first-person projects, goals, study/work descriptions and preferences, or from an explicit "recuerda que..." request. Members can add notes with `!memoria guardar <dato>`, list them with `!memoria recuerdos`, and delete a single item with `!memoria borrar <ID>`. Facts are injected as reference data, not instructions, and current corrections take priority. Credentials, contact details, links, and clearly sensitive personal data are excluded from durable fact storage. Expired records are physically cleaned from Qdrant on a six-hour interval while Stella is receiving activity. It is not mixed with other members' memories. Disable it with `!memoria conversacion desactivar`; this erases conversation turns and keeps a minimal technical opt-out marker so a local setting cannot reactivate memory after a restart. `!memoria olvidar` removes all available memories and the opt-in marker; `!ia olvidar` clears the saved conversation turns while keeping the opt-in and durable facts active. `!memoria conversacion olvidar` likewise clears the recent transcript but preserves durable facts. `!memoria olvidar` deletes all available memory for that person in the current room. If Qdrant is unavailable, Stella continues without durable continuity and does not claim that a save succeeded. Prompts sent to Gemini or Groq are processed by the selected provider; that provider's own retention policy still applies. Users can inspect their memory status with `!memoria` and `!memoria conversacion`.

SQLite stores only data required by existing community features:
- Account operations: UID, nickname, points and last-seen timestamp. Accounts and their associated points/reward state are deleted after 90 days of inactivity.
- Profile text: bio and status are cleared after 30 days.
- Affinity scores: retained only while both associated accounts remain active; removed when either account reaches the 90-day inactivity limit.
- Gifts: at most five per recipient and no more than 30 days.
- Daily reward/work cooldowns and personal music playlists: removed when the associated account reaches the 90-day inactivity limit.
- Moderation warnings: 90 days.
- Aggregate daily activity counts: 7 days.
- Polls and voter identifiers: 30 days after poll creation.
- Events and attendee identifiers: 30 days after the event date.
- Room settings and automatic-reply rules: retained as community configuration until deleted or the database is removed.

Anti-spam fingerprints, command sessions, voice participants, TTS/music queues, webhook de-duplication and cooldowns remain in bounded or expiring process memory. When enabled, AI conversation turns are the exception and are restored from Qdrant after a restart. No conversation text is included in activity metrics. SQLite cleanup runs at startup and at least hourly while the process receives events.

The Anarchy API's bot messaging capabilities do not mean the bot can delete or mute another member's messages. A warning is an internal moderation record, not a platform-enforced punishment. Link inspection is heuristic and does not certify that a destination is safe.

## Configuration

Set these variables in the hosting provider. Never commit real credentials.

| Variable | Required | Purpose |
|---|---:|---|
| `ANARCHY_BOT_TOKEN` | Yes | Anarchy bot API token |
| `ANARCHY_WEBHOOK_SECRET` | Yes | HMAC webhook signing secret |
| `ADMIN_UIDS` | No | Administrator UIDs separated by comma, semicolon or newline |
| `SINNER_UID` | No | Exact owner UID; exclusive access to global chat silence controls |
| `COMMAND_PREFIX` | No | Command prefix; defaults to `!` |
| `ANARCHY_API_BASE` | No | Anarchy API base URL |
| `DATA_DIR` | No | SQLite directory; defaults to `./data` |
| `GEMINI_API_KEY` | No | Gemini primary provider key |
| `GEMINI_MODEL` | No | Defaults to `gemini-3.5-flash-lite` |
| `QDRANT_URL` | No | Qdrant Cloud endpoint |
| `QDRANT_API_KEY` | No | Qdrant API key; keep secret |
| `QDRANT_COLLECTION` | No | Defaults to `stella_messages` |
| `QDRANT_AI_MEMORY_COLLECTION` | No | Defaults to `stella_ai_memory`; separate durable conversation history |
| `AI_MEMORY_RETENTION_DAYS` | No | Durable conversation-memory expiry; defaults to 30 days |
| `AI_MEMORY_CLEANUP_INTERVAL_SECONDS` | No | Cleanup interval for expired conversation memories; defaults to 21600 (six hours) |
| `GEMINI_EMBEDDING_MODEL` | No | Defaults to `gemini-embedding-2` |
| `GEMINI_EMBEDDING_DIMENSIONS` | No | Defaults to `768` |
| `MEMORY_RETENTION_DAYS` | No | Defaults to `30` |
| `MEMORY_CLEANUP_INTERVAL_SECONDS` | No | Defaults to `21600` (six hours) |
| `GEMINI_API_URL` | No | Gemini API base URL |
| `GROQ_API_KEY` | No | Groq fallback provider key |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b` |
| `GROQ_API_URL` | No | Groq Responses API base URL |
| `STELLA_AI_PRIMARY_PROVIDER` | No | `gemini` (default) or `groq` |
| `ANARCHY_COMMUNITY_BOT_TOKEN` | No | Optional separate token for LiveKit voice endpoints; defaults to the bot token |
| `JAMENDO_CLIENT_ID` | No | Optional Jamendo catalogue access |
| `TTS_VOICE` | No | Defaults to Chilean Spanish Catalina |
| `PROMETHEUS_METRICS_TOKEN` | No | Optional Bearer token protecting `/metrics` |

AI uses Gemini first and automatically tries Groq if Gemini fails. Both provider keys are optional, but at least one is required for `!ia` and `!traducir`. Persistent conversation memory, when explicitly enabled by a member, is stored in the separate `stella_ai_memory` Qdrant collection and expires after the configured retention period. It is not stored in SQLite. The external providers' own processing and retention policies still apply. Web search uses DuckDuckGo's HTML search endpoint and may be unavailable if the provider blocks the request.

Voice commands require an active Anarchy call and working LiveKit credentials from the Anarchy voice API. TTS additionally requires Edge TTS, PyAV and NumPy. Music playback uses only validated tracks from open-license catalogues (Internet Archive, Wikimedia Commons, Openverse, ccMixter, Audius and optionally Jamendo via `JAMENDO_CLIENT_ID`). YouTube is not used as a LiveKit audio source because its API policies prohibit downloading or relaying copies of audiovisual content without written permission. Voice rooms, TTS queues, music queues, search selections and downloaded audio are temporary and are removed on room exit or process shutdown.

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

The app exposes `GET /health`, `GET /metrics` and receives Anarchy webhooks at `POST /webhook` (also `POST /`). Set `PROMETHEUS_METRICS_TOKEN` if the metrics endpoint should require a Bearer token.

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

- Commands work when sent as a reply to any member's message; commands that need a target (social actions, roast, affinity and duel) automatically use the replied-to author when no explicit target is supplied. An explicit target always takes precedence.
