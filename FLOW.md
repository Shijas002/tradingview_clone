# FLOW: How the System Works

This document explains **how data moves** through the backend and **what every file does**.

---

## 1. The big picture

```
┌────────────────────┐        ┌───────────────────────────── FastAPI backend ─────────────────────────────┐
│  MetaTrader 5      │  IPC   │                                                                           │
│  terminal64.exe    │◄──────►│  MT5Provider ──┐                                                          │
└────────────────────┘ (poll) │                │ ticks        ┌──────────────────┐   per-client queues      │
                              │  Simulated  ───┼────────────► │  StreamManager   │ ───────────────────────► │──► WebSocket clients
                              │  Provider      │              │  + Candle        │   tick / candle / status │    (Phase 2 chart UI,
                              │                │              │    Aggregators   │                          │     ws_test_client.py)
                              │  OandaProvider─┤              └──────────────────┘                          │
                              │  "auto" ───────┘ (MT5 → OANDA failover; Phase 4 adds Upstox etc.)         │
                              │        ▲                                                                  │
                              │        │ history / symbols                                                │
                              │  REST API (/api/...) ◄────────────────────────────────────────────────────│◄── HTTP clients
                              │  GET /viewer ──► static/viewer.html (chart page using REST + WebSocket)   │
                              └───────────────────────────────────────────────────────────────────────────┘
```

- **Providers** are the only code that talks to a data source (MT5, OANDA, the simulator, or a broker later).
- The **`auto`** provider wraps MT5 and OANDA and serves whichever is healthy (details in FAILOVER.md).
- The **StreamManager** is the hub. It knows which clients want which symbols and builds live candles.
- The **API layer** is thin. REST handles one-off requests (history, symbols), and the WebSocket handles live streaming.

---

## 2. Startup flow (`python run.py`)

1. `run.py` loads and validates settings from `backend\.env` (via `app/core/config.py`).
2. `uvicorn` imports `app.main:app`, and `create_app()` runs.
3. `setup_logging()` starts the console log plus `logs\app.log`.
4. The **lifespan** startup runs:
   - `ProviderRegistry` creates one provider for each name in `ENABLED_PROVIDERS`.
   - `StreamManager` registers itself as the **tick handler** and **status handler** on every provider.
   - `registry.connect_all()` connects each provider:
     - **Simulator:** starts its random-walk loop immediately.
     - **MT5:** calls `mt5.initialize()` on its dedicated thread, then starts the **supervisor** task.
     - If MT5 fails (for example, the terminal is closed), the server **still starts** and the supervisor keeps retrying in the background.
5. The server listens on `APP_HOST:APP_PORT`.

---

## 3. Live streaming flow (one subscription)

1. **Client connects** to `ws://127.0.0.1:8000/ws/market`.
   - `api/websocket.py` accepts the socket and calls `StreamManager.register_client()`.
   - The client receives a `welcome` message with provider statuses and timeframes.
2. **Client subscribes**: `{"action":"subscribe","provider":"mt5","symbol":"EURUSD","timeframe":"M1"}`
   - `api/websocket.py` validates the JSON against the models in `models/market.py`.
   - `StreamManager.subscribe()` then does three things:
     - If this is the **first** client for `mt5:EURUSD`, it calls `provider.subscribe("EURUSD")`, which makes MT5 run `symbol_select`.
     - If this is the first client for `mt5:EURUSD:M1`, it fetches the **latest bar** and creates a **CandleAggregator** seeded with it. That way the live candle continues the real bar.
     - It records the client in its reference-count maps.
   - The client receives `subscribed`, including the current forming candle.
3. **Ticks flow.**
   - **MT5:** every `MT5_POLL_INTERVAL_MS`, the supervisor calls `symbol_info_tick()` for each subscribed symbol. Unchanged ticks are skipped and new ones become `Tick` objects.
   - **Simulator:** every `SIM_TICK_INTERVAL_MS`, it generates a new price.
   - The provider calls `StreamManager.handle_tick(tick)`.
4. **Fan-out** happens inside `handle_tick`:
   - A `tick` message goes to every client watching that symbol.
   - Each aggregator for that symbol (one per timeframe) runs `update(price, time)`, which either extends the current bar or opens a new one.
   - A `candle` message goes to every client subscribed to that symbol and timeframe.
5. **Delivery**
   - Messages are put on each client's **bounded queue**.
   - Each client's **writer task** sends them over the socket.
   - A slow client only loses its own **oldest** messages and never slows the feed for anyone else.
6. **Unsubscribe or disconnect**
   - Reference counts go down.
   - When nobody watches a timeframe any more, its aggregator is removed.
   - When nobody watches a symbol any more, `provider.unsubscribe()` stops polling it.

---

## 4. History flow (REST)

- `GET /api/history?provider=mt5&symbol=EURUSD&timeframe=M5&count=500`
  1. `api/rest.py` validates the query and checks `count <= HISTORY_MAX_BARS`.
  2. `registry.get("mt5").get_history(...)` runs on the MT5 thread:
     - `symbol_info` / `symbol_select` makes sure the symbol exists.
     - `copy_rates_from_pos(symbol, tf, 0, count)` returns the newest bars.
  3. It returns `{"candles":[{time, open, high, low, close, volume}, ...]}` in ascending time order.
- **Scroll-back (used by Phase 2):** add `&to=<time of oldest bar on screen>`.
  - MT5 then uses `copy_rates_from(symbol, tf, to - 1s, count)` and returns only **older** bars.

---

## 5. Reconnection flow (MT5)

- **Every ~2 seconds** the supervisor calls `terminal_info()` as a health check:
  - It returns `None`, so the IPC link is lost. The supervisor calls `mt5.shutdown()`, broadcasts `status: disconnected`, and re-initialises with back-off (1s, 2s, 4s … up to `MT5_RECONNECT_MAX_DELAY_S`).
  - It returns `connected = False`, so the terminal is running but offline from the broker. The supervisor broadcasts the status and keeps polling until it comes back.
- **On a successful reconnect**, all subscribed symbols are re-selected automatically, so clients don't need to resubscribe.
- **Manual reconnect:** `POST /api/providers/mt5/reconnect`.

---

## 5b. Failover flow (`auto` provider)

- Every second, and on every MT5/OANDA status change, `FailoverProvider` decides which source should be active:
  - **MT5 connected:** use MT5. If OANDA is currently active, wait `FAILOVER_SWITCH_BACK_DELAY_S` seconds first.
  - **MT5 down, OANDA up:** use OANDA.
  - **Both down:** keep the current choice and report `connected: false`.
- On a switch:
  1. It releases all subscriptions on the old source and acquires them on the new one (`EURUSD.m` becomes `EUR_USD` for OANDA).
  2. It broadcasts a `status` message with the new `details.active_source`.
  3. `StreamManager` re-seeds the live candles from the new source.
  4. The viewer shows a toast and reloads the chart.
- OANDA ticks and candles are shifted by the MT5 server offset, so the timeline matches MT5.

---

## 6. Live Viewer flow (`/viewer`)

1. The browser requests `GET /viewer`, and `main.py` returns `app/static/viewer.html`.
2. The page calls `GET /api/health`.
   - If it succeeds, the page runs in **LIVE mode** (`LiveSource` class).
   - If it fails (the file was opened directly or no backend is running), it runs in **DEMO mode** (`DemoSource` class, a JavaScript copy of the simulator).
3. The page loads its dropdown data: `GET /api/providers` fills the provider list and `GET /api/symbols` fills the symbol suggestions.
4. It draws the chart:
   - `GET /api/history` returns 500 candles, which go into `candleSeries.setData()` and the volume histogram.
5. It goes live:
   - It opens `ws://.../ws/market` and sends `subscribe`.
   - Each `candle` message goes to `candleSeries.update()`, which moves the last bar or adds a new one.
   - Each `tick` message updates the price, bid, ask, and spread in the toolbar.
   - Each `status` message updates the provider dots in the status bar.
6. Scroll-back: when you drag near the left edge, the page calls `GET /api/history?to=<oldest bar>`, prepends the result, and keeps your view in place.
7. If the WebSocket drops, the page reconnects with back-off (1s up to 15s), resubscribes, and reloads the chart.

---

## 7. WebSocket message reference

- **Client → server**
  - `{"action":"subscribe","provider":"mt5","symbol":"EURUSD","timeframe":"M1"}`
  - `{"action":"unsubscribe","provider":"mt5","symbol":"EURUSD","timeframe":"M1"}`
  - `{"action":"list_subscriptions"}`
  - `{"action":"ping","ts":1700000000000}`
- **Server → client**
  - `welcome`: `client_id`, `providers[]`, `timeframes[]`
  - `alert`: `data{alert_id, name, symbol, condition, price, level, message, triggered_at}` (Phase 3)
  - `alerts_changed`: alerts were created, edited, triggered or expired (Phase 3)
  - `journal_changed`: a journal entry was added, edited, synced or deleted (Phase 5)
  - `journal_status`: the Notion connection state changed (Phase 5)
  - `subscribed`: `provider`, `symbol`, `timeframe`, `candle` (the current forming bar)
  - `unsubscribed`: `provider`, `symbol`, `timeframe`
  - `tick`: `data{provider, symbol, time_ms, bid, ask, last, volume, price}`
  - `candle`: `provider`, `symbol`, `timeframe`, `data{time, open, high, low, close, volume}`
  - `status`: `data{name, connected, message, details}`, broadcast whenever a provider connects or disconnects
  - `subscriptions`: `items[]`
  - `pong`: `ts`, `server_time_ms`
  - `error`: `message`, `request` (the message that caused it)

---

## 8. Time conventions

- `Candle.time` is the bar **open time** in **UNIX seconds**, which is exactly what `lightweight-charts` expects.
- `Tick.time_ms` is in **UNIX milliseconds**.
- MT5 times are **broker server time** and are passed through unchanged, so the chart matches your MT5 terminal exactly.
- Weekly bars start on **Sunday 00:00**, and monthly bars start on the **1st**. Both match MT5.
- Candle `volume` is **tick volume** (number of ticks), which matches MT5's `tick_volume`.
- **Upstox (Phase 4):**
  - Times are **real UTC**. Volume is **real traded quantity**.
  - Intraday bars are anchored to the session open: 09:15 IST for NSE/BSE, 09:00 IST for MCX.
  - Daily, weekly and monthly bars are stamped 00:00 UTC of their Indian trading date.
- **What the chart shows:** the chart *displays* times in the zone chosen in ⚙ → Time zone (default "Exchange": IST for Upstox, broker time for MT5/auto, UTC otherwise). The stored timestamps never change.

---

## 9. File-by-file reference

### Project root

- **`README.md`**: overview, roadmap, and where config values go.
- **`TUTORIAL.md`**: step-by-step Windows setup and troubleshooting.
- **`FLOW.md`**: this document.
- **`FAILOVER.md`**: OANDA fallback setup and behaviour.
- **`GUIDE.md`**: how to use the chart app.
- **`frontend/`**: the React chart app (see section 10).
- **`.gitignore`**: keeps `.env`, logs, virtual environments, and `node_modules` out of git.

### `backend/` (top level)

- **`.env.example`**: template for every setting. Copy it to `.env` and fill it in.
- **`.env`**: *(you create this)* your real paths and keys. Never commit it.
- **`requirements.txt`**: runtime dependencies. `MetaTrader5` installs only on Windows.
- **`requirements-dev.txt`**: runtime dependencies plus `pytest` and `httpx` for tests.
- **`pytest.ini`**: points pytest at the `tests/` folder.
- **`run.py`**: entry point. Validates config, then starts uvicorn with the host and port from `.env`.

### `backend/app/core/` (foundation)

- **`config.py`**
  - The `Settings` class: a typed view of `.env`, validated on startup.
  - Always reads `backend\.env`, whichever folder you launch from.
  - Catches common mistakes, such as quoted Windows paths or a non-numeric `MT5_LOGIN`.
- **`logging_config.py`**
  - Sends logs to the console and to a rotating `logs\app.log` (5 MB × 5 files).
- **`exceptions.py`**
  - Domain errors (`SymbolNotFoundError`, `ProviderUnavailableError`, …), each with an HTTP status code.

### `backend/app/models/` (data shapes)

- **`market.py`**
  - `Timeframe` enum (M1 … MN1) with seconds and labels.
  - `bucket_start()`: works out which bar a timestamp belongs to (including week and month rules).
  - `Candle`, `Tick`, `SymbolInfo`, `ProviderStatus`, `HistoryResponse`.
  - WebSocket request models: `SubscribeMessage`, `UnsubscribeMessage`, `PingMessage`, `ListSubscriptionsMessage`.

### `backend/app/providers/` (data sources)

- **`base.py`**
  - `MarketDataProvider`: the abstract interface every data source implements (`connect`, `disconnect`, `get_symbols`, `get_history`, `subscribe`, `unsubscribe`).
  - Supports multiple tick and status listeners, plus reference-counted `acquire` / `release` so several consumers can share one subscription.
  - `normalize_symbol()` gives each provider its own canonical symbol spelling.
  - `session_anchor_s(symbol)` (session-aligned bars) and `real_volume` (exchange volume instead of tick volume) are used by Upstox.
- **`mt5_provider.py`**
  - Wraps the `MetaTrader5` package.
  - Runs every MT5 call on one dedicated thread, because the package is blocking and not thread-safe.
  - The supervisor task handles tick polling, de-duplication, health checks, and reconnect with back-off.
  - Maps MT5 timeframes, symbols, and history arrays to the app's models.
  - Detects the broker's server-time offset from fresh ticks and saves it to `data\mt5_server_offset.json`.
- **`simulated_provider.py`**
  - A fake market for 7 symbols (EURUSD, GBPUSD, USDJPY, XAUUSD, BTCUSD, NIFTY50, RELIANCE).
  - Its history ends exactly at the live simulated price, and older pages join without gaps.
  - Lets you build and test everything without MT5 (weekends, CI, Phase 2 UI work).
- **`oanda_provider.py`**
  - OANDA v20 client: account and instrument discovery, bid candles, and a streaming price feed with reconnect.
  - Translates MT5 symbol names to OANDA names and shifts candles to broker server time on request.
- **`failover_provider.py`**
  - The `auto` provider: health checks, switching, switch-back delay, and tick re-labelling.
  - History comes from the active source first, then the other.
- **`upstox_provider.py`, `upstox_auth.py`, `upstox_instruments.py`, `upstox_feed.py`**: the Indian broker (see section 12).
- **`registry.py`**
  - Builds the providers listed in `ENABLED_PROVIDERS` (`auto` is always built last, on top of its two sources).
  - `connect_all()` connects them, and one failing provider never blocks the others.
  - `get(name)` looks up a provider for the API layer.

### `backend/app/services/` (business logic)

- **`candle_aggregator.py`**
  - Turns a stream of ticks into OHLCV bars for one symbol and timeframe.
  - Is seeded with the latest historical bar so the live candle is correct from the first tick.
  - Ignores late ticks that belong to an already-closed bar.
- **`stream_manager.py`**
  - `ClientSession`: one WebSocket, plus its bounded queue and writer task.
  - `StreamManager`: reference-counted subscriptions, one aggregator per symbol and timeframe, tick-to-candle fan-out, and status broadcasts.
  - Re-seeds live candles when the `auto` provider changes source.

### `backend/app/api/` (HTTP and WebSocket layer)

- **`rest.py`**: `/api/health`, `/api/providers`, `/api/providers/{name}/reconnect`, `/api/timeframes`, `/api/symbols`, `/api/history`, `/api/stream/stats`.
- **`broker.py`** (Phase 4): Upstox login endpoints. See section 12.
- **`journal.py`** (Phase 5): trade journal endpoints. See section 13.
- **`websocket.py`**: `/ws/market`. Validates incoming JSON, dispatches it to the StreamManager, and returns errors as `{"type":"error"}` messages instead of closing the socket.

### `backend/app/static/`

- **`viewer.html`**: the Live Viewer, a single-page chart.
  - Loads the `lightweight-charts` v4.2.3 library (© TradingView, Apache-2.0) from unpkg, with jsDelivr as a fallback.
  - Holds the TradingView dark theme, toolbar, legend, and status bar.
  - `LiveSource` talks to the backend, and `DemoSource` simulates prices in the browser.
  - Phase 2 replaces this with a full React app, and this page stays as a lightweight debug tool.

### `backend/app/main.py`

- `create_app()`: the FastAPI factory. Sets up logging, CORS (for the Phase 2 frontend), error handlers, and routers.
- Serves `GET /viewer` (the Live Viewer page) and redirects `/` to it.
- The **lifespan** starts providers and the stream manager, and shuts them down cleanly on `Ctrl+C`.

### `backend/scripts/` (tools)

- **`check_mt5.py`**: a standalone MT5 diagnostic covering Python bitness, package, path, login, a live tick, and history. Run it before starting the server.
- **`check_oanda.py`**: an OANDA diagnostic covering token, account, symbol mapping, candles, and live prices.
- **`check_notion.py`**: a Notion diagnostic covering key, database lookup or creation, columns, and an optional `--test` page.
- **`check_upstox.py`**: an Upstox diagnostic covering configuration, login, instrument lists, daily and 5-minute candles, and the live stream.
- **`ws_test_client.py`**: a command-line viewer for the live WebSocket stream (`--provider`, `--symbol`, `--timeframe`, `--seconds`, `--no-ticks`).

### `backend/tests/` (automated checks)

- **`test_candle_aggregator.py`**: bar bucketing (intraday, weekly, monthly), candle building, seeding, and late ticks.
- **`test_api.py`**: end-to-end REST, WebSocket, and viewer-page tests using the simulator, so they run without MT5.
- **`test_failover.py`**: a fake OANDA API plus a stub MT5. Covers symbol mapping, time shift, streaming, failover and switch-back, and a full app run.
- **`test_persistence_alerts.py`**: workspace, drawings, scripts, backup, and the alert engine (Phase 3).
- **`test_journal.py`**: journal CRUD, P&L/R, stats, CSV, screenshots and backup, plus a fake Notion API covering setup, missing columns, create/update/screenshot/trash, validation errors and retry, database auto-creation, and bad token/unshared database (Phase 5).
- **`test_upstox.py`**: a fake Upstox API, instrument CDN and WebSocket, plus protobuf fixtures made with Upstox's official schema. Covers decoding, session alignment, instruments and search, chunked history, live ticks with real volume, the OAuth flow, and the broker endpoints (Phase 4).

---

## 10. Frontend (Phase 2): how the chart app works

```
 React components (UI)            Chart engine (imperative)                 Workers / backend
 ─────────────────────            ─────────────────────────                 ─────────────────
 App ─ TopBar, DrawingToolbar      ChartController (one per chart) ───────► REST /api/history
     ─ ChartGrid ─ ChartPane ────►   ├─ main series + volume                WebSocket /ws/market
     ─ Watchlist, BottomPanel        ├─ DrawingManager + DrawingsPrimitive  (via api/stream.js)
     ─ dialogs, ContextMenu          ├─ IndicatorRuntime (per script) ────► pine.worker.js (PineTS)
          ▲                          │     └─ PinePrimitive (fills, shapes, labels…)   └─ request.security
          │ zustand store            └─ bar replay                                       → /api/history
          └──────── persisted workspace (localStorage; SQLite in Phase 3)
```

- **Loading a chart:** `ChartPane` calls `ChartController.load()`.
  1. It fetches 1000 bars from `/api/history`.
  2. It sends the bars to the Pine worker.
  3. It subscribes to `/ws/market`.
- **Live candles** do three things:
  - update the main series and volume,
  - append the bar in the worker,
  - schedule indicator recalculation (immediately on a new bar, otherwise at most once per second).
- **Indicators:** each one is an `IndicatorRuntime`.
  - It asks the worker to run its Pine source with the chosen inputs.
  - It turns `plot()` into chart series and `hline()` into price lines.
  - Everything else (fill, bgcolor, shapes, labels, lines, boxes, tables, trades) is drawn by a `PinePrimitive`.
  - Non-overlay scripts get their own pane.
- **Drawings:** stored as `{time, price}` points per symbol in the store.
  - `DrawingManager` handles mouse creation, selection, dragging and hit-testing.
  - It renders inside a chart primitive, so drawings move and zoom with the chart.
- **Pine Editor:** compiles in the worker (`inspect`), then saves the script to the store. Every chart instance of the script updates.

### Frontend file reference (`frontend/src/`)

- **`main.jsx`, `App.jsx`**: entry point, app shell, bootstrapping (providers + stream), keyboard shortcuts, dialogs.
- **`styles.css`**: TradingView-like dark/light theme.
- **`api/client.js`**: REST helpers (`getProviders`, `getSymbols`, `getHistory`).
- **`api/stream.js`**: shared WebSocket client with reference-counted subscriptions and auto-reconnect.
- **`store/useStore.js`**: zustand store holding layout, panes, indicators, drawings (with undo/redo), watchlist, scripts and settings, persisted.
- **`utils/market.js`**: timeframes, chart types, formatting, Heikin Ashi, time↔bar-index maths, colors.
- **`chart/ChartController.js`**: one chart. Handles series, chart types, history and scroll-back, live updates, panes, bar replay, coordinate conversion and mouse routing.
- **`chart/registry.js`**: paneId → controller map.
- **`chart/drawings/tools.js`**: tool catalogue, default styles, Fib levels.
- **`chart/drawings/geometry.js`**: hit-test and drawing helpers.
- **`chart/drawings/DrawingManager.js`**: drawing model, interaction state machine and canvas rendering for all 16 tools.
- **`chart/drawings/DrawingsPrimitive.js`**: plugs the manager into lightweight-charts (renderer, price-axis labels, cursor hit-test).
- **`chart/pine/IndicatorRuntime.js`**: runs one Pine script and maps its result onto the chart.
- **`chart/pine/PinePrimitive.js`**: renders bgcolor, fill, plotshape/char/arrow, label, line, box, polyline, linefill, table and strategy trades.
- **`pine/pine.worker.js`**: Web Worker running PineTS. Includes a market-data provider so `request.security` can fetch other timeframes.
- **`pine/pineClient.js`**: promise wrapper for the worker, plus error-location parsing.
- **`pine/builtins.js`**: the 35 built-in indicators and strategies (Pine v6 sources) and the editor templates.
- **`editor/pineLanguage.js`**: Pine syntax highlighting and autocomplete for CodeMirror.
- **`components/`**
  - `TopBar.jsx`, `DrawingToolbar.jsx`, `ChartGrid.jsx`
  - `ChartPane.jsx`: legend, quote, replay bar
  - `DrawingStyleBar.jsx`, `Watchlist.jsx`, `BottomPanel.jsx`, `PineEditor.jsx`, `StrategyTester.jsx`, `StatusBar.jsx`, `ContextMenu.jsx`
  - `ui.jsx`: dropdown, modal, buttons
  - `Icons.jsx`: SVG icons
- **`components/dialogs/`**: `SymbolSearch`, `IndicatorsDialog`, `IndicatorSettings`, `ChartSettings`, `TextDialog`.
- **`__tests__/`**: unit tests for maths, drawing interactions and every built-in Pine script (`npm test`).

### Backend changes for Phase 2

- **`app/main.py`**: serves `frontend/dist` at `/app/`, and `/` redirects there.

---

## 11. Persistence & alerts (Phase 3)

```
 Browser (zustand store) ── serverSync.js ──► PUT /api/workspace, /api/drawings/{key}, /api/scripts/{id}
          ▲                    (debounced 0.7 s, only what changed)          │
          └── startup: GET /api/workspace + /api/drawings + /api/scripts ◄───┤  SQLite
                                                                             │  backend/data/tradingview.db
 Alert dialog ──► POST /api/alerts ──► AlertEngine.reload() ─────────────────┘
                                         │ acquires the symbol feed (ref-counted)
 provider ticks ──► AlertEngine._on_tick ─┴─► condition met? ──► AlertEvent row
                                                              ├─► WebSocket {"type":"alert"} → toast/sound/notification
                                                              └─► POST ALERT_WEBHOOK_URL (optional)
```

- **Startup**
  - The app loads the workspace from SQLite, then picks providers.
  - On the very first run the browser's existing Phase 2 data is uploaded instead.
- **Saving:** every change to layout, charts, indicators, drawings, watchlist, scripts or settings is debounced and written back. Failed writes retry every 5 s.
- **Alerts**
  - Alerts live in the `alerts` table.
  - The engine holds only *active* alerts in memory, grouped by symbol feed.
  - It compares each tick with the previous one to detect crossings.
  - Trend-line alerts compute the line's price at the tick time.
  - "Once" alerts become **triggered**; others respect their cool-down (1 min or 1 s).
  - Expired alerts are switched off every 10 s.

### Backend files (Phase 3)

- **`app/db/database.py`**: SQLAlchemy engine (SQLite with WAL), session helper, `create_all()` on startup.
- **`app/db/models.py`**: tables `workspaces`, `drawings`, `scripts`, `alerts`, `alert_events` (JSON columns keep the UI's structures as-is).
- **`app/api/persistence.py`**: workspace, drawings and scripts CRUD, plus export/import.
- **`app/api/alerts.py`**: alerts CRUD, trigger log, engine status, test notification.
- **`app/services/alert_engine.py`**: tick-driven evaluation, feed subscriptions, event storage, WebSocket broadcast, webhook.
- **`app/services/stream_manager.py`**: new `broadcast()` used for `alert` / `alerts_changed` messages.
- **`tests/test_persistence_alerts.py`**: storage round-trips, backup/restore, validation, conditions, and a live alert over the WebSocket.

### Frontend files (Phase 3)

- **`store/serverSync.js`**: load from and save to the backend (diff-based, debounced, retrying).
- **`alerts/alerts.js`**: loading, notifications (toast, two-tone sound, browser notification), helpers.
- **`components/dialogs/AlertDialog.jsx`**: create/edit alerts (price, channel, trend line; trigger, expiry, message).
- **`components/AlertsPanel.jsx`**: alert list (pause/resume/edit/test/delete) and trigger log.
- **`components/RightPanel.jsx`**: Watchlist / Alerts tabs.
- **`chart/ChartController.js`**: `setAlertLines()` draws active alerts as dashed 🔔 price lines.

---

## 12. Indian broker: Upstox (Phase 4)

### Login

```
Option A  .env UPSTOX_ANALYTICS_TOKEN (1 year, read-only) ─────────────┐
                                                                       ▼
Option B  [Log in to Upstox] → GET /api/broker/upstox/login         UpstoxAuth.active_token()
          → 302 to api.upstox.com/v2/login/authorization/dialog         │
            (client_id, redirect_uri, random state)                     │
          ← user logs in at Upstox                                      │
          → GET /api/broker/upstox/callback?code&state                  │
          → state checked, POST /v2/login/authorization/token           │
          → token + expiry (next 03:30 IST) saved in                    │
            backend/data/upstox_token.json (no name / e-mail)  ─────────┘
          → provider.on_auth_changed() restarts the live stream
          → the popup closes and the browser refreshes /api/broker/status
```

- Any HTTP 401 from Upstox marks that token as rejected. The status bar then asks for a new login, and the old token is never retried.

### Symbols

```
assets.upstox.com/.../NSE.json.gz, BSE.json.gz, MCX.json.gz   (public, refreshed by Upstox ~06:00 IST)
  → cached in backend/data/upstox/ (re-downloaded once a day)
  → parse_instruments(): keep EQ, INDEX, FUT (+ CE/PE if UPSTOX_INCLUDE_OPTIONS)
  → "NSE:RELIANCE" ⇄ "NSE_EQ|INE002A01018", digits from tick_size (paise), session anchor
```

### History (`GET /api/history?provider=upstox&symbol=NSE:RELIANCE&timeframe=H1`)

```
latest page:  GET /v3/historical-candle/intraday/{key}/minutes/60      (today)
always:       GET /v3/historical-candle/{key}/minutes/60/{to}/{from}   (≤ 1 quarter per call;
              1–15 min ≤ 1 month, daily ≤ 1 decade) - repeated backwards until `count` bars
→ rebucket(): IST → UTC, session-anchored bars (4h is built from 1h), D/W/M on the IST date
```

### Live

```
GET /v3/feed/market-data-feed/authorize  → wss URL
WebSocket ── binary JSON {"guid","method":"sub","data":{"mode":"full","instrumentKeys":[...]}}
          ◄─ protobuf FeedResponse (market_info, initial_feed, live_feed)
upstox_feed.decode_feed_response()  → ltp, ltt, bid/ask, vtt (total day volume)
→ Tick(price=ltp, volume=vtt delta, time=last trade time) → StreamManager → CandleAggregator
  (anchor 09:15 IST, real volume) → browser
market_info → status "Live - NSE open, BSE closed, MCX pre-open"
```

- Reconnects use back-off. Subscriptions are re-sent after a reconnect. A login change restarts the stream immediately.

### Backend files (Phase 4)

- **`app/providers/upstox_provider.py`**: the provider.
  - Supervisor (instruments → token → stream).
  - Chunked history plus `rebucket()`.
  - Subscriptions and feed-to-tick conversion.
  - Market status.
- **`app/providers/upstox_auth.py`**: picks the token (Analytics Token or the stored OAuth login), builds the login URL with a one-time `state`, exchanges the code, handles 03:30 IST expiry and rejected tokens.
- **`app/providers/upstox_instruments.py`**: download, cache and parse the instrument files. Provides `resolve()` (accepts `NSE:X`, `X`, `NSE_EQ:X` or an instrument key) and ranked `search()`.
- **`app/providers/upstox_feed.py`**: a dependency-free protobuf wire-format decoder for `MarketDataFeedV3.proto` (only the fields the chart needs; unknown fields are skipped).
- **`app/api/broker.py`**: `/api/broker/status`, `/upstox/login`, `/upstox/callback` (small HTML result page that closes the popup), `/upstox/logout`.
- **Changed files**
  - `models/market.py`: `bucket_start(..., anchor_s)`.
  - `candle_aggregator.py`: anchor and real volume.
  - `stream_manager.py`: builds aggregators from the provider's anchor and volume type.
  - `rest.py`: strips a Pine-style `EXCHANGE:` prefix for providers that don't use it.
  - `config.py`: `UPSTOX_*` settings and `DATA_DIR`.

### Frontend files (Phase 4)

- **`src/broker/broker.js`, `src/broker/BrokerLogin.jsx`**: broker status in the store, the login popup (postMessage + polling), logout, and the "Log in to Upstox" button. The button appears in the status bar, symbol search, chart error box and ⚙ settings.
- **`src/chart/timezone.js`, `src/chart/useChartZone.js`**: the chart time zone.
  - Undoes the MT5 server-time shift.
  - Provides the formatters for lightweight-charts (`timeFormatter`, `tickMarkFormatter`), the status-bar clock and the trade list.
- **`ChartController.setTimezone()`**, the **Time zone** select and the **Indian broker** box in Chart settings.
- **`pine/pine.worker.js`**:
  - `NSE:` / `BSE:` / `MCX:` string literals survive PineTS (which drops the exchange in `request.security`).
  - Indian symbols get `syminfo.currency = "INR"` and the Asia/Kolkata timezone.

---

## 13. Trade journal + Notion (Phase 5)

### Logging a trade

```
Alt+J / "Log trade" / 📓 on a Long-Short drawing / right-click "Log trade at" / 📓 in Strategy Tester
  → JournalDialog (pre-filled from the chart, the drawing or the backtest trade;
                   a chart screenshot is captured automatically)
  → POST /api/journal  {side, entry/exit, qty, SL/TP, fees, setup, tags, rating, notes, screenshot: data:URL}
  → SQLite journal_entries row + image in backend/data/journal/<id>-<n>.png
    notion_status = "pending" (Notion configured) or "local"
  → broadcast {"type":"journal_changed"}  → every open tab reloads the Journal tab
```

### Notion mirror (`NotionJournal` worker)

```
startup: GET /v1/users/me (key ok?) → GET /v1/databases/{id} → data_sources[0].id
         (or GET /v1/data_sources/{id}, or POST /v1/databases on NOTION_PARENT_PAGE_ID)
       → GET /v1/data_sources/{ds}  → PATCH it with any missing journal columns
loop:  rows with notion_status pending/deleting, oldest first, ~3 requests/s
  new      → POST /v1/pages {parent: data_source_id, properties}
  edited   → PATCH /v1/pages/{id} {properties}   (page deleted in Notion → created again)
  image    → POST /v1/file_uploads → POST …/send (multipart) → "Screenshot" files property
             + PATCH /v1/blocks/{page}/children (image block; the old one is deleted on change)
  deleted  → PATCH /v1/pages/{id} {"in_trash": true} → row removed locally
  429/5xx/network → wait (Retry-After) and retry;  400 → entry "error" + message (Retry in the UI)
```

- The database is the queue, so nothing is lost if the backend stops or Notion is down. Pending entries sync on the next start.
- **Columns written:**
  - Trade (title)
  - Symbol, Side, Status, Result
  - Entry/Exit Time, Entry, Exit, Quantity, Stop Loss, Take Profit, Fees, P&L, R Multiple
  - Setup, Tags, Rating, Timeframe, Provider, Notes, Screenshot, Journal ID
- **P&L** = (exit − entry) × quantity × (+1 long / −1 short) − fees. **R** = (exit − entry) × direction ÷ |entry − stop|.
- **Times** are stored as real UTC. Drawing and backtest times from MT5/auto charts are converted from broker time.

### Backend files (Phase 5)

- **`app/db/models.py`**: `JournalEntry` (trade fields + Notion state: page id/url, status, error, image block, screenshot versions).
- **`app/api/journal.py`**: CRUD, screenshot upload (PNG/JPEG/WebP ≤ 8 MB, magic-byte checked), stats, CSV, Notion status/retry/sync.
- **`app/services/notion_journal.py`**: the Notion client and sync worker (`SCHEMA`, `parse_notion_id`, `trade_metrics`).
- **`app/api/persistence.py`**: backup export/import now includes the journal (without the image files).

### Frontend files (Phase 5)

- **`src/journal/journal.js`**:
  - loading (debounced on `journal_changed`)
  - `captureChart()`: chart image with a title bar
  - drafts from pane, drawing or backtest trade
  - P&L preview and time formatting in the chart time zone
- **`src/components/JournalPanel.jsx`**: stats, filters, search, trade table, sync icons, Notion chip, close/edit/delete, CSV.
- **`src/components/dialogs/JournalDialog.jsx`**: the log/edit form with live R:R, risk and P&L, plus screenshot capture.
- **Entry points:** `BottomPanel` (Journal tab), `DrawingStyleBar` (📓), `StrategyTester` (📓 per trade), `ContextMenu`, `App.jsx` (Alt+J, stream events).

---

## 14. How future phases plug in

- **Another Indian broker (Kite, Angel One):** add `app/providers/<broker>_provider.py` implementing `MarketDataProvider` (use `upstox_provider.py` as the template), add its name to `SUPPORTED_PROVIDERS` and one line in `registry.py`.
- **More journal fields:** add a column to `JournalEntry`, the field to `JournalBase` in `api/journal.py`, and an entry in `SCHEMA` + `_properties()` in `notion_journal.py`. The Notion column is created automatically on the next start.
