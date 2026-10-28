# TradingView Clone (Local, Real-Time)

A fully local TradingView-style charting platform built with FastAPI and React. It streams live Forex data from MetaTrader 5, will add Indian equity data from a broker of your choice, saves chart state to SQLite, and journals trades to Notion.

> **Current status: Phase 1 (Data Bridge) is complete.** The backend streams live ticks and candles over WebSockets and serves history over REST. A **Live Viewer** chart page is included so you can see the data right away. The full React chart UI arrives in Phase 2.

---

## Documentation map

- **README.md** (this file): what the project is, the phase roadmap, and where every config value goes.
- **[TUTORIAL.md](TUTORIAL.md)**: step-by-step Windows setup, from installing Python and MT5 to watching live data.
- **[FLOW.md](FLOW.md)**: how data moves through the system, plus a file-by-file explanation of the code.
- **[FAILOVER.md](FAILOVER.md)**: always-on data. Covers OANDA setup and how the automatic MT5 → OANDA switch works.

---

## Phase roadmap

- **Phase 1: Data Bridge** ✅ *(this delivery)*
  - FastAPI backend with REST and WebSocket endpoints
  - MetaTrader 5 provider with tick polling, history, and auto-reconnect
  - Simulated provider, so you can develop with no MT5 or broker account
  - Server-side live candle building for all 9 timeframes (M1 to MN1)
  - Diagnostic and test scripts, plus automated tests
  - **Live Viewer** at `http://127.0.0.1:8000/viewer`: a single-page TradingView-style chart (lightweight-charts) wired to the real REST and WebSocket endpoints
  - **Automatic failover:** the `auto` provider uses MT5 and switches to OANDA whenever MT5 is offline (see FAILOVER.md)
- **Phase 2: Chart UI** ⏳
  - React + Vite + `lightweight-charts` frontend with a TradingView look
  - Symbol search, timeframe switcher, live candles, and lazy-loading of older history
- **Phase 3: Persistence** ⏳
  - SQLite storage for layouts, drawings, and indicator settings (REST CRUD)
- **Phase 4: Indian broker** ⏳
  - A pluggable adapter for Upstox, Zerodha Kite Connect, or Angel One SmartAPI (you choose later)
- **Phase 5: Notion journal** ⏳
  - Log trades with screenshots and notes into a Notion database

---

## Quick start (Windows, PowerShell)

These commands assume you already have Python 3.12 (64-bit). If you're starting from zero, follow **TUTORIAL.md**.

```powershell
cd tradingview-clone\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
python run.py
```

- Open **http://127.0.0.1:8000/viewer** to see the live chart.
- Open **http://127.0.0.1:8000/docs** to try the REST API.
- In a second terminal, watch the live stream:
  - `python scripts\ws_test_client.py --symbol EURUSD`

---

## ⚙️ Where to put your paths and API keys

**Every setting lives in one file: `backend\.env`.** You create it by copying `backend\.env.example`.

- **Rules for editing `.env`**
  - Put the value on the line **below** the `# <-- YOU FILL` comment.
  - Never put a comment on the same line as a value.
  - Write Windows paths **without double quotes**, because inside double quotes `\t` turns into a TAB character.
  - Restart the server (`Ctrl+C`, then `python run.py`) after every change.

- **Needed now (Phase 1)**
  - `MT5_TERMINAL_PATH`: full path to `terminal64.exe`
    - Example: `MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe`
    - TUTORIAL.md, Step 3, shows how to find it.
  - `ENABLED_PROVIDERS`: `simulated,mt5,oanda,auto` (use just `simulated` until MT5/OANDA are set up).
  - `DEFAULT_PROVIDER`: `auto` (MT5 with OANDA fallback), or `mt5`, `oanda`, `simulated`.
  - `OANDA_API_TOKEN`: your free OANDA practice token (FAILOVER.md, Step 1).

- **Optional (Phase 1)**
  - `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`: fill **all three** or **none**.
    - With none, the bridge uses the account already logged in inside MT5 (simplest).
  - `MT5_SERVER_UTC_OFFSET_HOURS`: leave empty to auto-detect.
  - `OANDA_ACCOUNT_ID`, `OANDA_SYMBOL_MAP`, `FAILOVER_SWITCH_BACK_DELAY_S`: see FAILOVER.md.

- **Later phases (already in the file and safe to leave empty)**
  - `DATABASE_URL`: Phase 3
  - `BROKER`, plus `UPSTOX_*`, `KITE_*`, or `ANGEL_*`: Phase 4
  - `NOTION_API_KEY`, `NOTION_DATABASE_ID`: Phase 5

---

## Project layout (Phase 1)

```
tradingview-clone/
├── README.md               ← you are here
├── TUTORIAL.md             ← setup guide
├── FLOW.md                 ← architecture + file reference
├── FAILOVER.md             ← OANDA fallback setup + behaviour
├── .gitignore
└── backend/
    ├── .env.example        ← copy to .env and fill in
    ├── requirements.txt
    ├── requirements-dev.txt
    ├── pytest.ini
    ├── run.py              ← start the server
    ├── app/
    │   ├── main.py
    │   ├── static/         (viewer.html - the Live Viewer page)
    │   ├── core/           (config, logging, exceptions)
    │   ├── models/         (data shapes)
    │   ├── providers/      (MT5, OANDA, auto failover, simulator, registry)
    │   ├── services/       (candle aggregator, stream manager)
    │   └── api/            (REST + WebSocket)
    ├── scripts/            (check_mt5.py, check_oanda.py, ws_test_client.py)
    └── tests/
```

---

## Live Viewer: two modes

- **LIVE mode** (green badge): open `http://127.0.0.1:8000/viewer` while the backend is running.
  - History comes from `/api/history`, and live candles from `/ws/market`.
  - Use the provider dropdown to switch between `auto`, `mt5`, `oanda`, and `simulated`.
- **DEMO mode** (yellow badge): open `viewer.html` anywhere without the backend (for example, inside Claude or by double-clicking the file).
  - Prices are simulated inside the browser, so you can preview the look and feel.
- **Viewer URL options:** `/viewer?provider=mt5&symbol=EURUSD&tf=M5`
- **Controls**
  - Type a symbol and press Enter.
  - Click a timeframe button.
  - Scroll or drag left to load older history automatically.

---

## Endpoints at a glance

- **Pages**
  - `GET /viewer`: the Live Viewer chart (`/` redirects here)
  - `GET /docs`: interactive API documentation
- **REST** (base `http://127.0.0.1:8000`)
  - `GET /api/health`: server and provider status
  - `GET /api/providers`: detailed provider status
  - `POST /api/providers/{name}/reconnect`: force a reconnect
  - `GET /api/timeframes`: supported timeframes
  - `GET /api/symbols?provider=mt5&query=EUR`: search symbols
  - `GET /api/history?provider=mt5&symbol=EURUSD&timeframe=M5&count=500`: candles
  - `GET /api/history?...&to=<unix_seconds>`: older candles (for scroll-back)
  - `GET /api/stream/stats`: active live feeds
- **WebSocket**: `ws://127.0.0.1:8000/ws/market`
  - Send `{"action":"subscribe","provider":"mt5","symbol":"EURUSD","timeframe":"M1"}`
  - Receive `tick`, `candle`, `status`, and `error` messages (full protocol in FLOW.md)
