# FAILOVER: Always-On Data (MT5 → OANDA)

When MetaTrader 5 is closed, crashed, or offline, the **`auto`** provider switches every chart to **OANDA** data within about a second. When MT5 comes back and stays healthy, it switches back automatically.

---

## How it behaves

- **Normal:** `auto` streams from **MT5**. The status bar shows `🟢 auto → mt5`.
- **MT5 goes down:** within ~1 second every subscribed symbol moves to **OANDA**.
  - The status bar shows `🟢 auto → oanda` and a toast says *"Data source switched: mt5 → oanda"*.
  - The chart reloads its history from OANDA, and live candles continue.
- **MT5 comes back:** data switches back after MT5 has stayed connected for `FAILOVER_SWITCH_BACK_DELAY_S` seconds (default 15). This stops a flaky connection from flipping the chart back and forth.
- **Both down:** `auto` shows 🔴 and keeps your subscriptions. Data resumes by itself as soon as either source recovers.
- **Symbols stay the same:** you keep using your MT5 names (`EURUSD`, `EURUSD.m`, `XAUUSD`, `US30` …). They are translated to OANDA names (`EUR_USD`, `XAU_USD`, `US30_USD` …) automatically.
- **Times stay the same:** MT5 shows broker *server* time (often UTC+2 or UTC+3), while OANDA uses UTC. OANDA data is shifted by your broker's offset so both sources sit on one timeline.
  - The offset is **auto-detected** from live MT5 ticks and remembered in `backend\data\mt5_server_offset.json`.
  - You can also set it yourself with `MT5_SERVER_UTC_OFFSET_HOURS`.

> **Prices differ slightly between sources.** OANDA and your MT5 broker are different liquidity providers, so expect small price and spread differences (usually a few points) while on the fallback.

---

## Step 1: Get a free OANDA practice token

- **Check availability first.** OANDA doesn't accept residents of every country, and that includes demo sign-ups in some places. If you can't open a practice account, tell me and I'll plug in a different fallback (for example Twelve Data) using the same `auto` system.
- **Open a practice (demo) account**
  - Go to https://www.oanda.com and choose **Try a free demo** (practice account).
- **Generate an API token**
  - Log in to the OANDA hub, go to **Tools → API** (it may also appear as **Manage API Access**), and click **Generate**.
  - Copy the token. It's a long string of letters and digits with a dash in the middle.
- **Keep it secret.** It's stored only in `backend\.env`, which git ignores.

---

## Step 2: Put the token in `backend\.env`

- Open `backend\.env` (`notepad .env`) and set these lines:
  - `OANDA_API_TOKEN=` followed by your token on the same line (no quotes)
  - `OANDA_ENVIRONMENT=practice`
  - `ENABLED_PROVIDERS=simulated,mt5,oanda,auto`
  - `DEFAULT_PROVIDER=auto`
- **Optional settings**
  - `OANDA_ACCOUNT_ID=`: leave empty to use the first account on the token.
  - `MT5_SERVER_UTC_OFFSET_HOURS=`: leave empty for auto-detect, or set e.g. `2`.
  - `FAILOVER_SWITCH_BACK_DELAY_S=15`
  - `OANDA_SYMBOL_MAP=`: only needed for unusual broker symbol names (see Step 5).
- If you created `.env` before this update, copy the new **OANDA** and **Failover** blocks from `.env.example` into it.

---

## Step 3: Install the new dependency and run the diagnostic

- In PowerShell, from `backend` with the virtual environment active:
  - `pip install -r requirements-dev.txt`
  - `python scripts\check_oanda.py`
  - `python scripts\check_oanda.py XAUUSD` (to try another symbol)
- **✅ Check:** the last line reads **"OANDA is ready to act as the fallback data source."**
- `pytest` should now report **17 passed**.

---

## Step 4: Test the failover yourself

1. Start MT5, then `python run.py`, then open http://127.0.0.1:8000/viewer.
2. Make sure the provider dropdown says **auto** and the status bar shows `auto → mt5`.
3. **Close MetaTrader 5.**
   - **✅ Check:** within a few seconds you see the toast *"Data source switched: mt5 → oanda"*, the status bar shows `auto → oanda`, and candles keep moving.
4. **Start MT5 again** and log in.
   - **✅ Check:** about 15 seconds after MT5 reconnects, the source switches back to `mt5`.
- You can also watch it from the API at http://127.0.0.1:8000/api/providers. Look for `"active_source"` under the `auto` entry.

---

## Step 5: Symbol mapping

- **Mapped automatically**
  - Forex pairs: `EURUSD`, `EURUSD.m`, `EURUSDm`, `EUR/USD` all become `EUR_USD`.
  - Metals: `XAUUSD` becomes `XAU_USD`, and `XAGUSD` becomes `XAG_USD`.
  - Common index and commodity names: `US30`, `US500`, `USTEC`/`NAS100`, `GER40`, `UK100`, `JP225`, `USOIL`/`XTIUSD`, `UKOIL`/`XBRUSD`, `XNGUSD`.
- **When a symbol can't be mapped** you'll see an error naming it, and it's listed under `unmapped_symbols` in `/api/providers`.
  - List OANDA's names at http://127.0.0.1:8000/api/symbols?provider=oanda.
  - Then add a mapping to `.env`, for example: `OANDA_SYMBOL_MAP=GER40=DE30_EUR,BTCUSD=BTC_USD`
- **Indian equities (NIFTY, RELIANCE …) are not on OANDA.** Those get their own broker feed in Phase 4.

---

## Using OANDA directly

- Choose **oanda** in the viewer's provider dropdown to chart OANDA data on its own.
  - It uses OANDA names (`EUR_USD`) and UTC times.
- REST examples:
  - `http://127.0.0.1:8000/api/symbols?provider=oanda&query=EUR`
  - `http://127.0.0.1:8000/api/history?provider=oanda&symbol=EUR_USD&timeframe=M5&count=200`

---

## How it works (code)

- **`app/providers/oanda_provider.py`**: the OANDA v20 client.
  - Loads the account and instrument list.
  - History comes from `/v3/instruments/{instrument}/candles` (bid prices).
  - Live prices come from one long-lived HTTP stream, `/v3/accounts/{id}/pricing/stream`, re-opened whenever the symbol list changes.
  - Reconnects with back-off. A rejected token stops retrying and says so clearly.
  - `normalize_symbol()` does the MT5 → OANDA name translation.
  - `get_history(..., time_shift_s=N)` shifts and re-buckets candles onto broker server time.
- **`app/providers/failover_provider.py`**: the `auto` provider.
  - Listens to MT5 and OANDA status and ticks, and checks health every second.
  - Moves all subscriptions on a switch and re-emits ticks as provider `auto` under the original symbol name.
  - For history, tries the active source first, then the other.
- **`app/providers/mt5_provider.py`**: now also detects the broker's server-time offset from fresh ticks (3 agreeing readings required) and saves it.
- **`app/providers/base.py`**: providers support several listeners and reference-counted `acquire` / `release`, so `auto` and direct `mt5`/`oanda` users never cancel each other's subscriptions.
- **`app/services/stream_manager.py`**: when `auto` changes source (or the time offset changes), it re-seeds the live candles from the new source so the forming bar is correct.
- **`app/static/viewer.html`**: shows `auto → source` in the status bar, shows the toast, and reloads the chart on a switch.
- **`scripts/check_oanda.py`**: an OANDA diagnostic.
- **`tests/test_failover.py`**: an in-memory fake OANDA API plus a stub MT5. Tests symbol mapping, time shift, streaming, failover and switch-back, and a full app run over the WebSocket.
