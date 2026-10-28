# TradingView Clone (Local, Real-Time)

A fully local TradingView-style charting platform built with FastAPI and React. It streams live Forex data from MetaTrader 5 (with OANDA fallback) and live Indian market data (NSE, BSE, MCX) from Upstox, saves chart state to SQLite, and journals trades to Notion.

> **Current status: all 5 phases are complete.** The backend streams live data from MT5, with automatic OANDA fallback, and Indian stocks, indices and futures from Upstox. The React chart app at **http://127.0.0.1:8000** has indicators, drawing tools, a Pine Script editor, a strategy tester, bar replay, multi-chart layouts and a watchlist. Everything is saved in SQLite, server-side price alerts keep running even with the browser closed, and a trade journal (with chart screenshots) syncs to Notion.

---

## Documentation map

- **README.md** (this file): what the project is, the phase roadmap, and where every config value goes.
- **[TUTORIAL.md](TUTORIAL.md)**: step-by-step Windows setup, from installing Python and MT5 to watching live data.
- **[FLOW.md](FLOW.md)**: how data moves through the system, plus a file-by-file explanation of the code.
- **[FAILOVER.md](FAILOVER.md)**: always-on data. Covers OANDA setup and how the automatic MT5 → OANDA switch works.
- **[GUIDE.md](GUIDE.md)**: how to use the chart app (indicators, drawings, Pine Editor, Strategy Tester, Replay, shortcuts).

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
- **Phase 2: Chart UI** ✅
  - React + Vite + `lightweight-charts` v5 app with a TradingView look (dark and light themes)
  - 9 chart types, 9 timeframes, symbol search, live candles, scroll-back history, log/percent scales
  - **35 built-in indicators and strategies**, all written in Pine Script and fully editable
  - **Pine Editor** (Pine v5/v6 via the PineTS engine) with error highlighting and `request.security` for other timeframes
  - **Strategy Tester** with performance metrics, equity curve and trade list
  - **16 drawing tools**: trend lines, rays, horizontal/vertical lines, channel, Fibonacci, rectangle, brush, text, price range, long/short position. Includes magnet, lock/hide, undo/redo and per-drawing styles
  - **Bar Replay**, multi-chart layouts (1–4 charts), watchlist with live prices, chart screenshots, keyboard shortcuts
- **Phase 3: Persistence & alerts** ✅
  - SQLite database (`backend\data\tradingview.db`) for layouts, charts, indicators, drawings, watchlist, Pine scripts and settings, synced automatically
  - Backup **export/import** (one JSON file)
  - **Server-side alerts** on prices, channels (rectangles) and trend lines:
    - Conditions: crossing, crossing up/down, greater/less than, entering/exiting a channel
    - Triggers: once, once per minute, or every time
    - Expiry dates and custom messages
  - Alert notifications: toast + sound + browser notification, plus an optional webhook (Discord/Slack/n8n…) and an alert log
- **Phase 4: Indian broker (Upstox)** ✅
  - `upstox` provider covering NSE, BSE and MCX: stocks, indices (NIFTY, BANKNIFTY, SENSEX…), futures, and options if you turn them on
  - Two ways to log in:
    - a 1-year read-only **Analytics Token** in `.env` (no daily login)
    - **daily OAuth login** with one click in the app (the token ends at 03:30 IST)
  - History from Upstox's V3 candle API: split automatically into several requests and stitched together; today's bars are merged in
  - Live ticks from the **Market Data Feed V3** WebSocket (protobuf), with real traded volume and bid/ask
  - TradingView-style symbols (`NSE:RELIANCE`), session-aligned 1h/4h bars (09:15 IST), daily instrument-list refresh
  - **Chart time zone** setting: IST for Indian symbols by default, or any zone you choose
  - Pine `request.security("BSE:RELIANCE", …)` keeps the exchange; `syminfo.currency` is `INR`
  - Other brokers (Zerodha Kite, Angel One) can be added as another provider file using the same interface
- **Phase 5: Trade journal + Notion** ✅
  - **Journal** tab in the bottom panel. Each entry has:
    - side, entry/exit, quantity, stop/target, fees
    - setup, tags, an execution rating, notes, and a **chart screenshot**
  - Ways to log a trade:
    - **Alt+J**
    - the 📓 button on a Long/Short position drawing
    - right-click → "Log trade at price"
    - 📓 on any Strategy Tester trade
  - P&L, R multiple and stats (win rate, net P&L, profit factor, average R), plus filters, search and CSV export
  - Saved in SQLite first, then mirrored to a **Notion database**:
    - Missing columns are added automatically, or the database is created for you.
    - Screenshots are uploaded through Notion's File Upload API.
    - Edits update the Notion page, and deleting an entry moves its page to the Notion trash.
    - Rate limits are respected, failed syncs retry, and errors are shown per entry.

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

- Open **http://127.0.0.1:8000** for the full chart app (or `/viewer` for the simple Phase 1 viewer).
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
  - `ENABLED_PROVIDERS`: `simulated,mt5,oanda,upstox,auto`. Providers you haven't set up yet just show as "not configured".
  - `DEFAULT_PROVIDER`: `auto` (MT5 with OANDA fallback), or `mt5`, `oanda`, `simulated`.
  - `OANDA_API_TOKEN`: your free OANDA practice token (FAILOVER.md, Step 1).

- **Optional (Phase 1)**
  - `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`: fill **all three** or **none**.
    - With none, the bridge uses the account already logged in inside MT5 (simplest).
  - `MT5_SERVER_UTC_OFFSET_HOURS`: leave empty to auto-detect.
  - `OANDA_ACCOUNT_ID`, `OANDA_SYMBOL_MAP`, `FAILOVER_SWITCH_BACK_DELAY_S`: see FAILOVER.md.

- **Database & alerts (Phase 3, optional)**
  - `DATABASE_URL`: where the SQLite file lives (default `backend\data\tradingview.db`).
  - `ALERT_WEBHOOK_URL`: also POST every triggered alert as JSON to this URL.

- **Indian markets: Upstox (Phase 4, optional).** See TUTORIAL.md, Step 15.
  - **Option A (recommended):** `UPSTOX_ANALYTICS_TOKEN`, a 1-year read-only token from the Upstox developer apps page (Analytics tab).
  - **Option B:** `UPSTOX_API_KEY` + `UPSTOX_API_SECRET` of your Upstox app. Then click **Log in to Upstox** in the app once a day.
    - `UPSTOX_REDIRECT_URI` must equal the app's Redirect URL: `http://127.0.0.1:8000/api/broker/upstox/callback`.
  - `UPSTOX_EXCHANGES`, `UPSTOX_INCLUDE_OPTIONS`, `UPSTOX_FEED_MODE`: optional tuning.

- **Notion journal (Phase 5, optional).** See TUTORIAL.md, Step 16.
  - `NOTION_API_KEY`: the Internal Integration Secret (`ntn_…`) from notion.so/profile/integrations.
  - `NOTION_DATABASE_ID`: your journal database's ID or URL. Share the database with the integration first.
  - Or `NOTION_PARENT_PAGE_ID`: a page shared with the integration. The "Trading Journal" database is created there.

---

## Project layout

```
tradingview-clone/
├── README.md               ← you are here
├── TUTORIAL.md             ← setup guide
├── FLOW.md                 ← architecture + file reference
├── FAILOVER.md             ← OANDA fallback setup + behaviour
├── GUIDE.md                ← how to use the chart app
├── frontend/               ← React chart app (Phase 2)
│   ├── dist/               ← ready-built app served by the backend (no Node.js needed)
│   ├── src/                ← source code (chart engine, Pine engine, components)
│   └── package.json
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
    │   ├── providers/      (MT5, OANDA, Upstox, auto failover, simulator, registry)
    │   ├── services/       (candle aggregator, stream manager, alert engine, Notion journal sync)
    │   ├── db/             (SQLite models + session)
    │   └── api/            (REST, persistence, alerts, broker login, journal, WebSocket)
    ├── scripts/            (check_mt5.py, check_oanda.py, check_upstox.py, check_notion.py, ws_test_client.py)
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

## Third-party components & licenses

- **TradingView Lightweight Charts™** (Apache-2.0): the chart rendering library. Keep the small "TV" attribution logo visible.
- **PineTS** by LuxAlgo (AGPL-3.0, commercial licence available): runs Pine Script in the browser.
  - Running this project privately on your own PC is fine.
  - If you ever distribute it or offer it to other people over a network, AGPL requires sharing your source code.
- **CodeMirror 6** (MIT), **React** (MIT), **zustand** (MIT).
- "TradingView" and "Pine Script" are trademarks of TradingView, Inc. This project is not affiliated with TradingView.

---

## Endpoints at a glance

- **Pages**
  - `GET /app/`: the React chart app (`/` redirects here once it's built)
  - `GET /viewer`: the simple Phase 1 viewer
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
- **Persistence (Phase 3)**
  - `GET/PUT /api/workspace`: layout, charts, watchlist, settings
  - `GET /api/drawings`, `PUT/DELETE /api/drawings/{provider:symbol}`
  - `GET /api/scripts`, `PUT/DELETE /api/scripts/{id}`
  - `GET /api/export`, `POST /api/import`: full backup / restore
- **Alerts (Phase 3)**
  - `GET/POST /api/alerts`, `PUT/DELETE /api/alerts/{id}`, `POST /api/alerts/{id}/test`
  - `GET/DELETE /api/alerts/events`: trigger log
  - `GET /api/alerts/engine`: what the alert engine is watching
- **Broker (Phase 4)**
  - `GET /api/broker/status`: Upstox login state
  - `GET /api/broker/upstox/login`: redirects to Upstox's login page
  - `GET /api/broker/upstox/callback`: Upstox returns here after login
  - `POST /api/broker/upstox/logout`: forget the daily login
  - Indian data uses the normal endpoints with `provider=upstox`, e.g. `/api/history?provider=upstox&symbol=NSE:RELIANCE&timeframe=H1`
- **Journal (Phase 5)**
  - `GET/POST /api/journal`, `GET/PUT/DELETE /api/journal/{id}`, `GET /api/journal/{id}/screenshot`
  - `GET /api/journal/stats`, `GET /api/journal/export.csv`
  - `GET /api/journal/notion`: Notion sync status
  - `POST /api/journal/notion/retry`, `POST /api/journal/{id}/sync`
- **WebSocket**: `ws://127.0.0.1:8000/ws/market`
  - Send `{"action":"subscribe","provider":"mt5","symbol":"EURUSD","timeframe":"M1"}`
  - Receive `tick`, `candle`, `status`, `alert`, `alerts_changed`, `journal_changed`, `journal_status`, and `error` messages (full protocol in FLOW.md)
