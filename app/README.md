# Shlakbaum — ANPR + Barrier + Telegram admin

FastAPI app that drives two Hikvision ANPR cameras (entry/exit) over ISAPI:
- license-plate CRUD in PostgreSQL
- auto-close barrier when camera sees a plate
- every 5 s alternates a `close` command between camera 1 and 2
- Telegram bot for whitelisted phones (`/open`, `/close`, `/status`)
- admin panel for plates, phones, plate↔phone links, manual barrier, logs
- Excel export with filters (year / month / day / plate)
- every open/close and every ANPR event is logged to the DB

## ISAPI endpoints used
From `ISAPI_Vehicle Access Control Management_ANPR Cameras.pdf`:
- `PUT /ISAPI/Parking/channels/<channelID>/barrierGate` (§11.6.3.1) — ctrlMode=open/close/lock/unlock
- `GET /ISAPI/Parking/channels/<channelID>/barrierGate/barrierGateStatus` (§11.6.3.2)
- Event push (§9.1.3.2 Listening Flow) — device → `POST /isapi/anpr/{role}` (multipart, `anpr.xml`)

Auth: HTTP Digest (per ISAPI framework §3.3).

## Setup
```bash
cd app
cp .env.example .env         # fill in cameras + PG + telegram token
./run.sh                     # creates .venv, installs deps, runs alembic, starts uvicorn
```

`run.sh` is idempotent and always runs alembic migrations first, then uvicorn.

## Camera side — one-time config
Tell each camera to push ANPR events to this server:
```
PUT http://<camera>/ISAPI/Traffic/ANPR/alarmHttpPushProtocol
<AlarmHttpPushProtocol version="2.0"><baseLineProtocolEnabled>true</baseLineProtocolEnabled></AlarmHttpPushProtocol>
```
then set the HTTP listening host to `${PUBLIC_LISTENER_URL}/isapi/anpr/entry`
(camera 1) and `${PUBLIC_LISTENER_URL}/isapi/anpr/exit` (camera 2), via
`PUT /ISAPI/Event/notification/httpHosts`.

## Admin panel
- `/login` — login (creds from `.env`: `ADMIN_USERNAME`, `ADMIN_PASSWORD`)
- `/` — dashboard + quick barrier buttons
- `/plates` — plate CRUD
- `/users` — Telegram user (phone) CRUD
- `/links` — tie a plate to a phone → notifications on entry/exit
- `/logs` — entry/exit + barrier action log; Excel export
- `/cameras` — per-camera open/close/lock/unlock + live status

## Layout
```
app/
├── alembic/                  # migrations (0001 creates all tables)
├── src/
│   ├── main.py               # FastAPI lifespan (registry + scheduler + bot)
│   ├── config.py             # pydantic-settings (loads .env)
│   ├── database.py           # async SQLAlchemy
│   ├── models.py             # Plate, TelegramUser, PlatePhoneLink,
│   │                         # EntryExitLog, BarrierActionLog
│   ├── schemas.py            # pydantic I/O
│   ├── security.py           # session-based admin auth
│   ├── isapi/
│   │   ├── client.py         # async httpx + digest + XML barrier control
│   │   ├── parser.py         # anpr.xml parser
│   │   └── registry.py       # process-wide camera registry
│   ├── services/
│   │   ├── barrier.py        # open/close + audit log
│   │   ├── anpr.py           # on-event: log + auto-close + notify
│   │   └── telegram_notify.py
│   ├── bot/bot.py            # aiogram bot (phone-authenticated)
│   ├── scheduler/alternating_close.py  # 5 s rotating close loop
│   └── routers/              # plates, phones, links, logs, export,
│                             # barrier, isapi_events, admin_ui
└── templates/                # Jinja2 admin panel
```
