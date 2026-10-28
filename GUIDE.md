# GUIDE: Using the Chart App

Open **http://127.0.0.1:8000** while the backend is running (it redirects to the chart app at `/app/`).

---

## 1. Screen layout

- **Top bar**
  - Symbol button, the 9 timeframes, chart type, **Indicators**, **Replay**, undo/redo.
  - On the right: layout, screenshot, settings, and panel toggles.
- **Left toolbar:** drawing tools, magnet, stay-in-drawing-mode, lock, hide, remove all, reset view.
- **Chart area:** 1, 2, 3 or 4 charts. Click a chart to make it *active*; the top bar always controls the active chart.
- **Right panel:** watchlist with live prices.
- **Bottom panel:** **Pine Editor**, **Strategy Tester** and **Journal**.
- **Status bar:** live connection, provider health (including `auto → mt5/oanda`, and **Log in to Upstox** when needed), save state, and a clock in the chart's time zone.

---

## 2. Symbols, timeframes and chart types

- **Change symbol**
  - Click the symbol button, or click the symbol name in the chart's legend.
  - Or just start typing on the keyboard (like TradingView).
- **Symbol search dialog**
  - **Provider chips:** choose `auto`, `mt5`, `oanda`, `upstox` (Indian markets) or `simulated`.
  - **↑ / ↓ + Enter** pick a result; **★** adds it to the watchlist.
- **Timeframes:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 1M.
- **Chart types:** Candles, Hollow candles, Heikin Ashi, Bars, Line, Step line, Area, Baseline, Columns.
- **Scroll back in time:** drag the chart left and older history loads automatically.
- **Price scale** (right-click the chart, or ⚙ settings): Regular, Logarithmic, Percent, Indexed to 100, Invert.

---

## 3. Indicators

- **Add:** click **Indicators** (or press **/**), search, then click to add. The dialog stays open so you can add several.
- **Built-in library (35):**
  - **Trend:** SMA, EMA, WMA, HMA, VWMA, MA Cross, MA Ribbon, Supertrend, Parabolic SAR, Ichimoku, Pivot Points.
  - **Volatility:** Bollinger Bands, Keltner, Donchian, Envelope, ATR, Bollinger BandWidth.
  - **Oscillators:** RSI, MACD, Stochastic, Stochastic RSI, CCI, DMI/ADX, Williams %R, Momentum, ROC, Awesome Oscillator, TSI, Aroon.
  - **Volume:** Volume, VWAP, OBV, MFI, Chaikin Money Flow.
  - **Strategies:** MA Cross, RSI Reversal, Bollinger Breakout.
- **Legend controls:** hover an indicator's line in the top-left legend to get these buttons:
  - 👁 show/hide
  - ⚙ settings (inputs)
  - `</>` open its Pine source in the editor
  - ✕ remove
- **Settings dialog:** generated from the script's `input.*()` calls (numbers, sources, colors, timeframes, options…). Click **Defaults** to reset.
- **Live updates:** indicators recalculate on every new bar and at most once per second while a bar is forming.
- **Built-ins are real Pine:** every built-in is a Pine Script v6 file, so you can open it, copy it and change it.

---

## 4. Drawing tools

- **Lines**
  - Trend line (**Alt+T**), Ray, Extended line, Arrow.
  - Horizontal line (**Alt+H**), Horizontal ray, Vertical line (**Alt+V**), Cross line.
- **Channels & Fibonacci:** Parallel channel (**Alt+C**, 3 clicks), Fib retracement (**Alt+F**).
- **Shapes & text:** Rectangle (**Alt+R**), Brush (press-drag-release), Text (double-click a text drawing to edit it).
- **Measure / trading:** Price range, Long position, Short position (entry, target and stop, with R:R and P&L for the quantity you set).
- **How to draw**
  - Pick a tool, then click for each point.
  - Or press and drag for 2-point tools.
  - **Esc** cancels.
- **Edit a drawing**
  - Click it to select it. Drag the body to move it, or drag a handle to reshape it.
  - The floating style bar gives you color, fill, width, line style, extend left/right, Fib prices, text size, quantity, lock, clone and delete.
- **Delete:** select the drawing, then press **Delete**. **Ctrl+Z / Ctrl+Y** undo and redo.
- **Magnet (🧲):** snaps points to the nearest open/high/low/close.
- **Stay in drawing mode (+):** keeps the tool active after each drawing.
- **Lock all / Hide all / Remove all:** at the bottom of the left toolbar.
- **Where drawings are kept:** they belong to the **symbol**, not the chart, so they appear on every timeframe and in every pane showing that symbol.

---

## 5. Pine Editor

- Open the bottom panel (▭ icon, top right) and choose the **Pine Editor** tab.
- **Write Pine Script v5/v6.** It runs locally through the open-source **PineTS** engine (see the license note in README.md).
- **Toolbar**
  - **New:** start from an Indicator or Strategy template.
  - **Open:** your saved scripts, or a copy of any built-in.
  - **Save (Ctrl+S):** saves to "My scripts". Every chart already using the script updates instantly.
  - **Add to chart (Ctrl+Enter):** saves, compiles and adds the script to the active chart.
- **Errors:** compile errors are underlined in the code and listed in the console on the right; runtime errors also appear there and in the legend.
- **Editor help:** autocomplete with **Ctrl+Space**; search with **Ctrl+F**.
- **Supported:** `plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `hline`, `fill`, `bgcolor`, `barcolor`, `label`, `line`, `box`, `polyline`, `linefill`, `table`, `input.*`, `strategy.*`, `alertcondition`, and `request.security` for other timeframes and symbols of the same provider.
- **Limits**
  - `request.security` timeframes must be one of 1, 5, 15, 30, 60, 240, D, W, M.
  - Very new or rare Pine features may not be implemented yet by PineTS.

---

## 6. Strategy Tester

- Add a strategy (built-in, or your own `strategy()` script), then open the **Strategy Tester** tab.
- **Overview tab**
  - Net profit, closed trades, percent profitable, profit factor, max drawdown, average trade, average win/loss.
  - Largest win/loss, open P&L, buy & hold, Sharpe/Sortino, plus the equity curve.
- **List of trades tab:** every closed trade with entry/exit time and price, size, profit, run-up and drawdown.
- **On the chart:** entry and exit markers are drawn on the chart itself.
- **What it tests on:** the backtest runs on the history currently loaded. Scroll left to load more history and it re-runs.

---

## 7. Bar Replay

1. Click **Replay** in the top bar.
2. Click the bar where the replay should start.
3. Use the controls:
   - **▶ / ⏸** play and pause
   - **⏭** one bar forward
   - speed from 0.5× to 10×
   - **⇤ Select bar** picks a new start
- Indicators and strategies recalculate bar by bar, exactly as in real time.
- **Exit replay** returns to live data.

---

## 8. Layouts, watchlist, screenshots

- **Layouts (⊟ icon):** Single, 2 side by side, 2 stacked, 1 + 2, Grid 2×2. Each chart has its own symbol, timeframe, chart type and indicators.
- **Watchlist**
  - **+** opens symbol search; **★** adds the active symbol.
  - Click a row to load it in the active chart; drag rows to reorder; **×** removes.
- **Screenshot (📷):** downloads a PNG of the active chart.
- **Theme:** ⚙ → Theme → Dark / Light.
- **Time zone:** ⚙ → Time zone.
  - **Exchange (auto)** is the default: IST for Indian symbols, broker server time for MT5/auto, UTC for OANDA and the simulator.
  - Or pick UTC, your computer's zone, Kolkata, London, New York…
  - This changes the time axis, crosshair label, Strategy Tester trade times and status-bar clock.

---

## 9. Indian markets (Upstox)

- **One-time setup:** see TUTORIAL.md, Step 15. You can use an Analytics Token (no daily login) or the daily login.
- **Daily login (only if you use API key + secret)**
  - Click **Log in to Upstox**: it's in the status bar, the chart's message box, symbol search, or ⚙ → Indian broker.
  - Log in on Upstox's page. The window closes by itself and the charts load.
  - Tokens end at 03:30 IST every night.
- **Find symbols:** symbol search → **upstox** chip.
  - Type `RELIANCE`, `NIFTY`, `BANKNIFTY`, `BSE:SENSEX`, `CRUDEOIL`…
  - Results show the exchange, type (EQ / INDEX / FUT) and lot size for futures.
- **Symbol names:** `NSE:RELIANCE`, `BSE:RELIANCE`, `NSE:NIFTY` (index), `NSE:NIFTY FUT 27 NOV 25`, `MCX:CRUDEOIL FUT 19 NOV 25`.
- **Bars:** 1h and 4h bars start at 09:15 IST (MCX 09:00), like TradingView. Volume is real traded quantity.
- **Market status:** hover `upstox` in the status bar (e.g. "Live - NSE open, BSE open, MCX open").
- **Everything else works the same:** indicators, Pine scripts, drawings, alerts, replay, watchlist.
  - In Pine use `request.security("NSE:RELIANCE", "D", close)`, or `"BSE:…"` to compare exchanges.
- **Logging out:** ⚙ → Indian broker → **Log out**.

---

## 10. Alerts

- **Create an alert** in any of these ways:
  - Click **Alert** in the top bar (or press **Alt+A**).
  - Right-click the chart and choose **🔔 Add alert at <price>**.
  - Select a drawing and click **🔔** in its style bar. This works on a trend line, ray, extended line, horizontal line/ray, rectangle or price range.
- **Conditions:** Crossing, Crossing up, Crossing down, Greater than, Less than, Entering channel, Exiting channel.
  - Rectangles become a channel between their top and bottom.
  - Trend lines follow the line's slope, extended or not.
- **Trigger:** Only once, once per minute, or every time (at most once per second).
- **Optional extras:** an expiration date/time, a name, and a message. The message can use `{{ticker}}`, `{{close}}` and `{{level}}`.
- **When an alert fires**
  - An orange toast and a sound appear in the app.
  - A desktop notification appears if the tab is in the background (allow notifications when the browser asks).
  - It's recorded in the **Alerts → Log** tab.
  - It's optionally POSTed to `ALERT_WEBHOOK_URL` (see `.env`).
- **Alerts run inside the backend**, so they keep working with the browser closed, as long as `python run.py` is running.
- **On the chart:** active alerts show as dashed orange 🔔 lines.
- **Manage alerts** in the right panel's **Alerts** tab: Pause / Resume / Restart, Edit, **Test** (sends a test notification), Delete.

---

## 11. Trade journal

- **Open it:** bottom panel → **Journal** tab.
- **Log a trade** in any of these ways:
  - **Log trade** button, or **Alt+J**: pre-filled with the active chart's symbol and current price.
  - Select a **Long/Short position** drawing → **📓**: entry, target, stop and quantity come from the drawing.
  - Right-click the chart → **📓 Log trade at <price>**.
  - **Strategy Tester → List of trades → 📓**: logs that backtest trade, tagged `backtest`.
- **The form**
  - Side, Open/Closed, entry price/time, quantity, stop loss, take profit, exit price/time, fees.
  - Setup (remembers your previous setups), tags (comma-separated), execution rating (★), notes.
  - It shows planned **R:R**, **risk**, and once closed, **P&L** and **R**.
- **Screenshot:** a picture of the chart (with your drawings and indicators) is attached automatically. **Retake** captures it again; **Remove** drops it.
- **The list**
  - **Close** an open trade: the exit is pre-filled with the current price.
  - **✎** edits (or double-click a row). **🗑** deletes.
  - Click the symbol to show it on the chart. **🖼** opens the screenshot.
  - Filter All/Open/Closed, "this symbol only", or search by symbol, setup, tag or note.
- **Stats:** trades, win rate, net P&L, profit factor, average R, open trades.
- **CSV:** downloads every trade for Excel.
- **Notion** (optional, TUTORIAL.md Step 16)
  - The chip at the top right shows the connection.
  - Each row shows **N✓** (in Notion; click to open), **⏳** (syncing) or **⚠** (failed: hover for the reason, click to retry).
  - Deleting a trade moves its Notion page to the trash.
- **P&L formula:** price difference × quantity − fees. For Forex, enter the quantity in units (100000 = 1 lot) to get P&L in the quote currency.

---

## 12. Keyboard shortcuts

- **/**: Indicators dialog
- **Any letter or number**: symbol search
- **Alt+T / H / V / F / R / C**: trend line / horizontal / vertical / fib / rectangle / channel
- **Esc**: cancel drawing, return to cursor
- **Delete / Backspace**: delete the selected drawing
- **Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z)**: undo / redo drawings
- **Ctrl+S / Ctrl+Enter** (in the Pine Editor): save / add to chart
- **Alt+A**: create alert
- **Alt+J**: log a trade in the journal
- **Right-click on a chart**: context menu (reset view, scales, copy price, remove all)

---

## 13. Where your work is saved

- **Everything saves automatically** to the backend's SQLite database (`backend\data\tradingview.db`) within a second of any change:
  - layout, charts, indicators and their settings, drawings, watchlist, Pine scripts, theme, alerts, journal (screenshots in `backend\data\journal\`).
- **Status bar:** shows **✓ Saved** (or **Saving…** / **⚠ Offline**).
- **If the backend is offline:** changes are kept in the browser and uploaded when it comes back.
- **Any browser on your PC** opens the same workspace.
- **Backup:** ⚙ settings → **Backup → Export** downloads one JSON file (journal included, screenshots not). **Import** restores it (this replaces the current data).
