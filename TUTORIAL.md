# Tutorial: Phase 1 Setup on Windows

Follow the steps in order. Each step ends with a **✅ Check** so you know it worked before you move on.

**Time needed:** about 20–30 minutes.

---

## Step 0: What you need

- Windows 10 or 11 (64-bit)
- An internet connection
- About 2 GB of free disk space
- No broker account is needed for Phase 1, because MT5's free demo account works fine.

---

## Step 1: Install Python 3.12 (64-bit)

- **Why 3.12:** the `MetaTrader5` package publishes Windows wheels for recent 64-bit Python versions, and 3.12 is the safest choice.
- **Install**
  - Go to https://www.python.org/downloads/windows/
  - Download **"Windows installer (64-bit)"** for the latest **Python 3.12.x**.
  - Run the installer and **tick "Add python.exe to PATH"** on the first screen.
  - Click **Install Now**.
- **✅ Check:** open a new PowerShell window and run:
  - `py -3.12 --version` should print `Python 3.12.x`
  - `py -3.12 -c "import struct; print(struct.calcsize('P')*8)"` should print `64`

---

## Step 2: Install MetaTrader 5 and open a demo account

- **Download**
  - From the official site: https://www.metatrader5.com/en/download
  - Or from your Forex broker's website. Brokers ship their own branded MT5 build, which is fine.
- **Install**
  - Run `mt5setup.exe` and accept the default folder (note it down for Step 3).
- **Log in**
  - On first launch, MT5 offers to open a demo account. Choose **MetaQuotes-Demo** (or your broker's demo server) and complete the form.
  - Or, if you already have an account, use **File → Login to Trade Account**.
- **Make sure data is flowing**
  - Open **View → Market Watch** (`Ctrl+M`). Prices should be ticking.
  - Right-click Market Watch and choose **Show All** so every symbol is available.
- **Note the exact symbol names**
  - Some brokers add suffixes such as `EURUSD.m` or `EURUSDm`. You'll use these exact names later.
- **✅ Check:** the bottom-right corner of MT5 shows a connection speed such as `123/4 kb`, not "No connection".

---

## Step 3: Find your `terminal64.exe` path

Use any one of these methods:

- **Method A: desktop shortcut**
  - Right-click the MetaTrader 5 shortcut and choose **Properties**.
  - Copy the **Target** field (remove any surrounding quotes).
- **Method B: Task Manager**
  - With MT5 running, open Task Manager → **Details** tab.
  - Right-click `terminal64.exe` and choose **Open file location**.
  - Copy the folder path from Explorer's address bar and add `\terminal64.exe`.
- **Method C: PowerShell search**
  - `Get-ChildItem "C:\Program Files" -Recurse -Filter terminal64.exe -ErrorAction SilentlyContinue | Select-Object FullName`
- **Typical results**
  - `C:\Program Files\MetaTrader 5\terminal64.exe`
  - `C:\Program Files\<Broker Name> MetaTrader 5\terminal64.exe`
- **⚠️ Do not use the "Data Folder"** (File → Open Data Folder). That's where MT5 stores settings, not where the program is installed.

---

## Step 4: Find the project on your PC

- The project is already in your folder: `C:\Users\280704\Documents\test\tradingview-clone`
- Open it in VS Code (optional but handy): `File → Open Folder` and pick that folder.
- **✅ Check:** `C:\Users\280704\Documents\test\tradingview-clone\backend\run.py` exists.

---

## Step 5: Create the Python virtual environment

Run these commands in PowerShell:

- Go to the backend folder:
  - `cd C:\Users\280704\Documents\test\tradingview-clone\backend`
- Create the environment:
  - `py -3.12 -m venv .venv`
- Activate it:
  - `.\.venv\Scripts\Activate.ps1`
  - **If you see "running scripts is disabled"**, run this once, then retry:
    - `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
- Install the dependencies (this includes `MetaTrader5` automatically on Windows). Run this again whenever `requirements.txt` changes:
  - `python -m pip install --upgrade pip`
  - `pip install -r requirements-dev.txt`
- **✅ Check:** your prompt starts with `(.venv)` and `python -c "import MetaTrader5; print(MetaTrader5.__version__)"` prints a version.
- **Remember:** activate the environment (`.\.venv\Scripts\Activate.ps1`) in **every new terminal** before running project commands.

---

## Step 6: Create and fill in your `.env` file ⚙️

- **Create it**
  - `Copy-Item .env.example .env`
  - `notepad .env`
- **Rules**
  - Write each value on the line **below** its `# <-- YOU FILL` comment.
  - **No quotes** around Windows paths.
  - **No comments** on the same line as a value.
- **Phase 1 values to set**
  - `MT5_TERMINAL_PATH=` followed by your path from Step 3
    - Example: `MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe`
  - `ENABLED_PROVIDERS=simulated,mt5,oanda,upstox,auto`
    - Providers you haven't set up yet just show as "not configured", so you can keep them all in the list.
  - `DEFAULT_PROVIDER=auto`
    - `auto` = MT5 with automatic OANDA fallback. You can also use `mt5`, `oanda`, or `simulated`.
- **Fallback data source (recommended)**
  - `OANDA_API_TOKEN=` your free OANDA practice token.
  - Full setup is in **FAILOVER.md** (Steps 1–3). Without a token, `auto` simply uses MT5 alone.
- **Optional login values** (fill all three or leave all three empty)
  - `MT5_LOGIN=` your account number (digits only)
  - `MT5_PASSWORD=` your account password
  - `MT5_SERVER=` the server name exactly as MT5 shows it, e.g. `MetaQuotes-Demo`
  - Leaving them empty uses whichever account is logged in inside MT5. **This is recommended.**
- **Indian markets (optional):** the `Upstox` block. See **Step 15**.
- **Notion trade journal (optional):** the `Notion` block. See **Step 16**.
- Save and close Notepad.

---

## Step 7: Run the MT5 diagnostic

- With MT5 open and logged in, run:
  - `python scripts\check_mt5.py`
  - For a symbol with a broker suffix: `python scripts\check_mt5.py EURUSD.m`
- **✅ Check:** the last line reads **"All checks passed - MT5 is ready for the data bridge."**
- If it fails, the message tells you why. Also see **Troubleshooting** below.

---

## Step 8: Run the automated tests

- `pytest`
- **✅ Check:** you see `42 passed`. These tests use the simulator and fake MT5/OANDA/Upstox servers, so they pass without any account.

---

## Step 9: Start the backend server

- `python run.py`
- **✅ Check:** the log shows these lines:
  - `Provider 'simulated' connected`
  - `Provider 'mt5' connected` (if enabled)
  - `Provider 'oanda' connected` (if your token is set)
  - `Provider 'upstox' connected` (always listed; its status says whether you are logged in)
  - `Provider 'auto' connected`
  - `Ready on http://127.0.0.1:8000`
- Logs are also written to `backend\logs\app.log`.
- Stop the server with `Ctrl+C`.

---

## Step 10: Explore the REST API

- Open http://127.0.0.1:8000/docs in your browser. It's an interactive API page.
- Try these:
  - `GET /api/providers`: both providers should show `"connected": true`
  - `GET /api/symbols` with `provider=mt5` and `query=EUR`
  - `GET /api/history` with `provider=mt5`, `symbol=EURUSD`, `timeframe=M5`, `count=100`
- Or use PowerShell:
  - `Invoke-RestMethod "http://127.0.0.1:8000/api/history?provider=mt5&symbol=EURUSD&timeframe=M5&count=5" | ConvertTo-Json -Depth 5`

---

## Step 11: Open the Live Viewer chart 📈

- With the server running, open **http://127.0.0.1:8000/viewer** in Chrome or Edge.
- **✅ Check:** the badge in the top-right corner shows **● LIVE · BACKEND** (green) and candles move in real time.
- **How to use it**
  - **Provider dropdown:** choose `auto` (MT5 with OANDA fallback), `mt5`, `oanda`, or `simulated`.
  - **Symbol box:** type a symbol (e.g. `XAUUSD`) and press **Enter**. The suggestions come from your broker.
  - **Timeframe buttons:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 1M.
  - **Scroll or drag left:** older candles load automatically.
  - **Status bar (bottom):** WebSocket state, provider health, and ticks per second.
- **Shortcut link:** `http://127.0.0.1:8000/viewer?provider=mt5&symbol=EURUSD&tf=M5`
- **If the badge shows "DEMO"** you opened the file directly instead of through the server. Use the URL above.
- **Internet needed:** the page loads the chart library from `unpkg.com` (falling back to `cdn.jsdelivr.net`). If both are blocked on your network, the chart area shows a message saying so.

---

## Step 12: Open the full chart app 🚀

- With the server running, open **http://127.0.0.1:8000** in Chrome or Edge (it redirects to `/app/`).
- **✅ Check:** you see the TradingView-style workspace (top bar, drawing toolbar, watchlist) with live candles.
- **No Node.js needed:** the app is already built (`frontend\dist`) and served by the backend.
- **How to use everything** (indicators, drawings, Pine Editor, Strategy Tester, Replay, shortcuts) is covered in **GUIDE.md**.

---

## Step 13 (optional): Rebuild the chart app after changing its code

- You only need this if you edit files in `frontend\src`.
- **Install Node.js 20 LTS or newer** from https://nodejs.org (Windows installer, defaults are fine).
  - **✅ Check:** `node --version` prints v20 or higher.
- In a new PowerShell window:
  - `cd C:\Users\280704\Documents\test\tradingview-clone\frontend`
  - `npm install` (first time only)
  - `npm run build` rebuilds `frontend\dist`. Refresh the browser afterwards.
  - `npm test` runs the 50 frontend tests.
- **Live development mode (optional):**
  - Keep the backend running, then run `npm run dev` and open **http://localhost:5173/app/**.
  - Code changes appear instantly.

---

## Step 14: Watch the raw stream in the terminal (optional)

- Open a **second** PowerShell window, then:
  - `cd C:\Users\280704\Documents\test\tradingview-clone\backend`
  - `.\.venv\Scripts\Activate.ps1`
- Watch the simulator:
  - `python scripts\ws_test_client.py --symbol EURUSD`
- Watch real MT5 data:
  - `python scripts\ws_test_client.py --provider mt5 --symbol EURUSD --timeframe M1`
- Candle updates only:
  - `python scripts\ws_test_client.py --provider mt5 --symbol XAUUSD --no-ticks`
- **✅ Check:** `TICK` and `CANDLE` lines scroll by. The candle's `C=` value follows the MT5 bid price.
- **Note:** Forex markets close on weekends, so MT5 sends no ticks from Friday night to Sunday night. Use the simulator then.

---

## Step 15 (optional): Connect Upstox for Indian markets 🇮🇳

Live NSE, BSE and MCX charts (stocks, indices such as NIFTY / BANKNIFTY / SENSEX, futures) come from your **Upstox** account.

- **What you need:** an active Upstox trading account. Nothing else is installed on your PC.
- **Step 15.1: Create an Upstox API app** (one time)
  - Open **https://account.upstox.com/developer/apps** and log in.
  - Or in Upstox go to **My account → Profile → Apps → My apps**.
  - Click **+ New app** and fill in the form:
    - **App name:** anything, e.g. `tvclone`
    - **Redirect URL:** `http://127.0.0.1:8000/api/broker/upstox/callback` (copy it exactly)
    - **Postback URL:** leave empty or use the same URL
  - Confirm. Your **API Key** and **API Secret** are then shown under **My apps**.
- **Step 15.2: Choose how the app logs in.** Pick **one** option:
  - **Option A: Analytics Token (recommended, no daily login)**
    - On the developer apps page open the **Analytics** tab, click **Generate Token**, confirm, and copy it with the copy icon.
    - The token is read-only (it cannot place orders) and valid for **1 year**.
    - In `backend\.env`, paste it on the line below `# <-- YOU FILL (option A)`:
      - `UPSTOX_ANALYTICS_TOKEN=eyJ0eXAiOi...` (one long line, no quotes)
  - **Option B: Daily login from the app**
    - In `backend\.env`, fill:
      - `UPSTOX_API_KEY=` your API Key
      - `UPSTOX_API_SECRET=` your API Secret
      - Keep `UPSTOX_REDIRECT_URI=http://127.0.0.1:8000/api/broker/upstox/callback`. It must match the app's Redirect URL exactly.
    - Upstox tokens end at **03:30 IST every night**, so you log in **once per day** (see Step 15.4).
- **Step 15.3: Enable the provider**
  - Make sure `ENABLED_PROVIDERS` contains `upstox`, e.g. `ENABLED_PROVIDERS=simulated,mt5,oanda,upstox,auto`.
  - Optional settings:
    - `UPSTOX_EXCHANGES=NSE,BSE,MCX`: which instrument lists to load.
    - `UPSTOX_INCLUDE_OPTIONS=true`: also search option contracts (about 100k more symbols).
    - `UPSTOX_FEED_MODE=ltpc`: a lighter stream with price only, no bid/ask.
  - Restart the backend (`Ctrl+C`, then `python run.py`).
  - On the first start it downloads the instrument lists into `backend\data\upstox\` (10–30 seconds, then once per day).
- **Step 15.4: Log in (Option B only)**
  - In the app, click **Log in to Upstox**. You'll find it in the status bar next to `upstox`, in the chart's error box, in symbol search, and under ⚙ → **Indian broker**.
  - A small window opens Upstox's login page. Log in there (mobile + OTP + PIN, as usual).
  - The window closes by itself and the status dot turns green.
  - The login is stored in `backend\data\upstox_token.json` until 03:30 IST. Only the token, its expiry and your Upstox client id are kept, never your name or e-mail.
- **Step 15.5: Check it**
  - `python scripts\check_upstox.py`
  - Or for another symbol: `python scripts\check_upstox.py NSE:NIFTY`
  - **✅ Check:** you see daily and 5-minute candles, then "Upstox is ready."
- **Step 15.6: Use it**
  - Symbol search → **upstox** chip → type `RELIANCE`, `NIFTY`, `BANKNIFTY`, `BSE:SENSEX`, `CRUDEOIL`…
  - Symbols are written TradingView-style, `EXCHANGE:SYMBOL`:
    - `NSE:RELIANCE`, `BSE:RELIANCE`, `NSE:NIFTY`
    - futures: `NSE:NIFTY FUT 27 NOV 25`
  - Times are shown in **IST** automatically. ⚙ → **Time zone** changes it for all charts.
  - 1h and 4h bars start at the session open (09:15 for NSE/BSE, 09:00 for MCX), like TradingView.
  - In Pine Script, `request.security("NSE:RELIANCE", "D", close)` and `request.security("BSE:RELIANCE", "60", close)` work. `syminfo.currency` is `INR`.

---

## Step 16 (optional): Copy your trade journal to Notion 📓

The **Journal** tab works without Notion: every trade is saved in `backend\data\tradingview.db`. With Notion connected, each trade is also copied into a Notion database, including a screenshot of the chart.

- **Step 16.1: Create a Notion integration** (one time)
  - Open **https://www.notion.so/profile/integrations** and click **+ New integration**.
  - Fill in the form:
    - **Name:** e.g. `TV Journal`
    - **Workspace:** yours
    - **Type:** Internal
  - Click **Save**.
  - Leave the capabilities at their defaults (Read, Update and Insert content).
  - Copy the **Internal Integration Secret** (it starts with `ntn_`).
  - In `backend\.env`, put it on the line below the comment: `NOTION_API_KEY=ntn_...`
- **Step 16.2: Tell the app where to write.** Pick **one** option:
  - **Option A (easiest): let the app create the database**
    - In Notion, create or choose a page, e.g. "Trading".
    - On that page click **•••** (top right) → **Connections** → **Connect to** → choose your integration.
    - Copy the page link (**Share → Copy link**) and paste it: `NOTION_PARENT_PAGE_ID=https://www.notion.so/Trading-1a2b3c...`
    - On the next start the backend creates a **"Trading Journal"** database on that page with all the columns. The log also prints its ID, which you can later put in `NOTION_DATABASE_ID`.
  - **Option B: use a database you already have**
    - Open the database as a full page → **•••** → **Connections** → add your integration.
    - Copy its link and paste it: `NOTION_DATABASE_ID=https://www.notion.so/...?v=...`. A full URL or just the 32-character ID both work.
    - Missing columns are **added** automatically (Side, Entry, Exit, P&L, R Multiple, Screenshot…).
    - Your existing columns are **never changed**. If one has the same name but a different type, it's skipped and the check script tells you.
- **Step 16.3: Check it**
  - Restart the backend.
  - Run `python scripts\check_notion.py --test`.
  - **✅ Check:** you see "[OK] Connected", then a test page link. The test page is moved to the trash right away.
- **Step 16.4: Use it**
  - Open the bottom panel → **Journal** tab → **Log trade**, or press **Alt+J**.
  - Each trade shows **N✓** when it's in Notion; click it to open the page.
  - **⏳** means waiting to sync. **⚠** means the sync failed: hover to read why, click to retry.
  - Trades you logged before setting up Notion are copied over automatically on the next start.

---

## Daily routine (after the first setup)

1. Open MetaTrader 5 and wait until it connects.
2. In PowerShell, start the backend:
   - `cd C:\Users\280704\Documents\test\tradingview-clone\backend`
   - `.\.venv\Scripts\Activate.ps1`
   - `python run.py`
3. Open **http://127.0.0.1:8000** in your browser.
4. Upstox with Option B only: click **Log in to Upstox** once (the token ends at 03:30 IST every night).

---

## Troubleshooting

- **`mt5.initialize() failed: [-10005] IPC timeout`**
  - Open MT5 manually and log in once, then restart the server.
  - Check that `MT5_TERMINAL_PATH` points to `terminal64.exe` (not the Data Folder).
  - Run PowerShell and MT5 as the **same Windows user**. Don't run one of them "as administrator" and the other not.
- **`[-6] Authorization failed`**
  - `MT5_LOGIN`, `MT5_PASSWORD`, or `MT5_SERVER` is wrong. Leave all three empty to use the terminal's logged-in account.
- **`MT5_TERMINAL_PATH contains a control character`**
  - Remove the double quotes around the path in `.env`.
- **`MT5_LOGIN must be your numeric account number`**
  - Use digits only, with nothing else on that line.
- **`Symbol 'EURUSD' not found in MT5`**
  - Your broker uses a suffix. List the real names here:
    - http://127.0.0.1:8000/api/symbols?provider=mt5&query=EURUSD
- **`MetaTrader5 package not available`**
  - The virtual environment isn't active, or you're using 32-bit Python. Repeat Steps 1 and 5.
- **MT5 shows `connected: false` with the message "not connected to the broker server"**
  - The terminal is running but offline. Check your internet connection and MT5 login. The bridge recovers automatically.
- **No ticks are arriving**
  - The market is closed (weekend or holiday), or the symbol isn't trading. Try `BTCUSD` if your broker offers crypto, or use the simulator.
- **Port 8000 is already in use**
  - Change `APP_PORT` in `.env` (e.g. to `8001`) and restart.
- **The viewer is blank or shows "DEMO" while the server is running**
  - Open it through the server (`http://127.0.0.1:8000/viewer`), not by double-clicking the file.
  - Press `Ctrl+F5` to hard-refresh.
- **`auto` shows "mt5 offline - using oanda fallback"**
  - That's the failover working. Start or reconnect MT5 and it switches back after ~15 seconds.
- **`oanda` shows "OANDA_API_TOKEN is empty" or "rejected the API token"**
  - See FAILOVER.md, Steps 1–3, and run `python scripts\check_oanda.py`.
- **http://127.0.0.1:8000/app/ says "Chart app not built"**
  - The folder `frontend\dist` is missing. Copy it again from the delivery, or rebuild it (Step 13).
- **The chart app is blank or stuck on "Waiting for a data provider"**
  - Check that `python run.py` is running and http://127.0.0.1:8000/api/providers shows at least one provider with `"connected": true`.
  - Press `Ctrl+F5`.
- **An indicator shows ⚠ in the legend**
  - Hover the warning to read the Pine error. Open the script with the `</>` button to fix it.
- **"⚠ Save failed" or "Offline" in the status bar**
  - The browser couldn't reach the backend. Your changes are kept in the browser and upload automatically once the backend is back.
- **Start over with an empty workspace**
  - Stop the server, then delete `backend\data\tradingview.db` (plus the `-wal` / `-shm` files next to it), and restart.
  - Export a backup first if you might need it (⚙ settings → Backup → Export).
- **Alerts don't fire**
  - Check the **Alerts** tab: the alert must show **ACTIVE**.
  - http://127.0.0.1:8000/api/alerts/engine shows each watched symbol and its last price.
  - Alerts need live ticks, so they don't fire while the market is closed.
- **`upstox` shows "not configured"**
  - Neither `UPSTOX_ANALYTICS_TOKEN` nor `UPSTOX_API_KEY` + `UPSTOX_API_SECRET` is set in `.env`. See Step 15, then restart.
- **The Upstox login window says "Upstox refused the login" or shows a redirect error on Upstox's page**
  - The Redirect URL of your Upstox app must be exactly `http://127.0.0.1:8000/api/broker/upstox/callback`, the same as `UPSTOX_REDIRECT_URI`.
  - If you changed `APP_PORT`, change the port in both places.
- **"Login link expired or was not started from this app"**
  - Always start the login from the app's button (not from a bookmarked Upstox page), and finish it within 10 minutes.
- **"Upstox rejected the access token"**
  - Option B: the daily token expired (03:30 IST). Click **Log in to Upstox** again.
  - Option A: generate a new Analytics Token, paste it into `.env`, and restart.
- **Upstox history works but the status says "live stream down (WebSocket access refused (403))"**
  - Your Upstox app has no market-data WebSocket permission yet. Check the app on the developer apps page, or ask Upstox support to enable "Market Data Feed".
  - Charts still load history and refresh when you reload.
- **Indian charts don't move**
  - NSE/BSE trade 09:15–15:30 IST and MCX 09:00–23:30 IST on weekdays. The status bar tooltip of `upstox` shows the market status, e.g. "NSE open".
- **An Indian symbol isn't found**
  - Search without the exchange first (e.g. `TATAMOTORS`).
  - Options appear only with `UPSTOX_INCLUDE_OPTIONS=true`.
  - Instrument lists refresh daily after 06:30 IST. To force a refresh, delete `backend\data\upstox\` and restart.
- **Journal says "Notion off"**
  - `NOTION_API_KEY` and `NOTION_DATABASE_ID` (or `NOTION_PARENT_PAGE_ID`) aren't set. The journal still saves locally. See Step 16.
- **"Notion rejected NOTION_API_KEY"**
  - Copy the Internal Integration Secret again (it starts with `ntn_`), paste it on one line, and restart.
- **"Notion database … not found or not shared"**
  - The database (or parent page) isn't connected to your integration. Open it in Notion → **•••** → **Connections** → add the integration, then click **Retry** in the Journal tab (or restart).
- **A journal entry shows ⚠**
  - Hover the ⚠ to read Notion's message, fix the cause, then click ⚠ (one entry) or **Retry** (all failed entries).
  - Example cause: a journal column was deleted in Notion. Restart the backend and it's added back.
- **You want more detail in the logs**
  - Set `LOG_LEVEL=DEBUG` in `.env` and restart.

---

## What's next

- All five phases are complete. Ideas for later:
  - another Indian broker (Kite or Angel One) as a second provider file
  - more Pine features as PineTS adds them
  - order placement (Upstox's order APIs need a whitelisted static IP)
