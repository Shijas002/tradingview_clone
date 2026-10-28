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

## Step 4: Get the project onto your PC

- Use a simple path **without spaces**, for example `C:\dev\tradingview-clone`.
- **Create the folders** (one PowerShell command):
  - `New-Item -ItemType Directory -Force -Path C:\dev\tradingview-clone\backend\app\core, C:\dev\tradingview-clone\backend\app\models, C:\dev\tradingview-clone\backend\app\providers, C:\dev\tradingview-clone\backend\app\services, C:\dev\tradingview-clone\backend\app\api, C:\dev\tradingview-clone\backend\app\static, C:\dev\tradingview-clone\backend\scripts, C:\dev\tradingview-clone\backend\tests`
- **Create each file** at the path shown in the chat and paste its code.
  - VS Code is the easiest editor for this: `File → Open Folder → C:\dev\tradingview-clone`.
  - Save every file as **UTF-8** (the default in VS Code and in Windows 11 Notepad).
  - File names must match exactly, including the dot in `.env.example` and `.gitignore`.
- **✅ Check:** `C:\dev\tradingview-clone\backend\run.py` exists.

---

## Step 5: Create the Python virtual environment

Run these commands in PowerShell:

- Go to the backend folder:
  - `cd C:\dev\tradingview-clone\backend`
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
  - `ENABLED_PROVIDERS=simulated,mt5,oanda,auto`
    - Use `simulated` alone if MT5 and OANDA aren't ready yet.
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
- **Leave the "FUTURE PHASES" block empty for now.**
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
- **✅ Check:** you see `17 passed`. These tests use the simulator, so they pass even without MT5.

---

## Step 9: Start the backend server

- `python run.py`
- **✅ Check:** the log shows these lines:
  - `Provider 'simulated' connected`
  - `Provider 'mt5' connected` (if enabled)
  - `Provider 'oanda' connected` (if your token is set)
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

## Step 12: Watch the raw stream in the terminal (optional)

- Open a **second** PowerShell window, then:
  - `cd C:\dev\tradingview-clone\backend`
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

## Daily routine (after the first setup)

1. Open MetaTrader 5 and wait until it connects.
2. In PowerShell, start the backend:
   - `cd C:\dev\tradingview-clone\backend`
   - `.\.venv\Scripts\Activate.ps1`
   - `python run.py`
3. Open **http://127.0.0.1:8000/viewer** in your browser.

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
- **You want more detail in the logs**
  - Set `LOG_LEVEL=DEBUG` in `.env` and restart.

---

## What's next

- When you're happy with Phase 1, reply **"Phase 1 confirmed"** and I'll build **Phase 2: the React + lightweight-charts chart UI**.
