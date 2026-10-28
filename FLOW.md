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

---

## 9. File-by-file reference

### Project root

- **`README.md`**: overview, roadmap, and where config values go.
- **`TUTORIAL.md`**: step-by-step Windows setup and troubleshooting.
- **`FLOW.md`**: this document.
- **`FAILOVER.md`**: OANDA fallback setup and behaviour.
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
  - **Phase 4 adds a broker by writing one new class that implements this interface.**
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
- **`ws_test_client.py`**: a command-line viewer for the live WebSocket stream (`--provider`, `--symbol`, `--timeframe`, `--seconds`, `--no-ticks`).

### `backend/tests/` (automated checks)

- **`test_candle_aggregator.py`**: bar bucketing (intraday, weekly, monthly), candle building, seeding, and late ticks.
- **`test_api.py`**: end-to-end REST, WebSocket, and viewer-page tests using the simulator, so they run without MT5.
- **`test_failover.py`**: a fake OANDA API plus a stub MT5. Covers symbol mapping, time shift, streaming, failover and switch-back, and a full app run.

---

## 10. How future phases plug in

- **Phase 2 (frontend):** a new `frontend/` folder (React + Vite).
  - It calls `/api/history` for the initial bars and `/api/history?to=` when you scroll back.
  - It subscribes over `/ws/market` and feeds `candle` messages into `series.update()`.
- **Phase 3 (SQLite):** adds `app/db/` and `app/api/layouts.py` for CRUD on charts, drawings, and indicators.
- **Phase 4 (broker):** adds `app/providers/<broker>_provider.py` and one line in `registry.py`. Nothing else changes.
- **Phase 5 (Notion):** adds `app/services/notion_journal.py` and `app/api/journal.py`.
