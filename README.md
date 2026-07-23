# XAU/USD Trading Bot

Aplikasi pembelajaran untuk market data, analisis, risk planning, paper trading, backtesting, dan eksekusi manual XAU/USD pada akun **demo** MetaTrader 5. Milestone 9 tetap menolak akun real/contest yang tidak sesuai, tidak mengaktifkan auto trading, dan tidak pernah memulai engine demo otomatis setelah restart.

## Status Milestone 7

Tersedia seluruh fondasi Milestone 1–6 serta:

- Backtest modular: `BacktestEngine`, `HistoricalDataService`, `BacktestStrategyRunner`, `BacktestRiskManager`, `BacktestExecutionSimulator`, `BacktestPositionManager`, `BacktestPnLCalculator`, `BacktestStatisticsService`, `EquityCurveService`, `DrawdownCalculator`, `BacktestReportService`, dan `BacktestStateManager`.
- Sumber candle historis MT5 read-only atau CSV; timeframe M1, M5, M15, M30, H1, H4, dan D1 tervalidasi.
- Strategi awal H1/M15/M5 memakai komponen analysis yang sama dengan paper trading, termasuk validasi sinkronisasi dan hard spread rejection.
- Risk backtest memakai `RiskManager`, `StopLossCalculator`, `TakeProfitCalculator`, `RiskRewardValidator`, dan `PositionSizeCalculator` yang sama dengan paper trading; override `strategy_settings`/`risk_settings` tervalidasi ketat sebelum job dibuat.
- Anti-look-ahead: hanya candle closed dengan close time tidak melewati decision time; entry baru dihitung pada open M5 berikutnya.
- Simulasi spread, adverse slippage, commission, swap directional, SL/TP, dan kebijakan same-bar konservatif `SL_FIRST`; floating equity bersifat net setelah commission dan accrued calendar-day swap.
- Background job persisten dengan status `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, progress, ETA, dan cooperative cancellation.
- Laporan, equity/drawdown curve, audit event, daftar trade/rejection, dan export CSV.
- Deployment tetap native: Python virtual environment, Vite build, Nginx, serta NSSM/PM2; tanpa Docker/container.

Belum tersedia dan tidak termasuk Milestone 7: order execution MT5, posisi asli akun demo, akun real, optimasi strategi, machine learning, atau dashboard lengkap.

Backend tidak otomatis terhubung ke MT5, paper engine tidak otomatis `RUNNING`, dan backtest hanya dimulai melalui `POST /api/v1/backtests`. Semua hasil simulasi berada di SQLite.

## Status Milestone 8

Dashboard React lengkap tersedia dengan dark responsive shell, sidebar/topbar, 11 route, TanStack Query, WebSocket market dengan reconnect/backoff, candlestick/equity/drawdown chart, toast, confirmation dialog, loading/empty/error state, dan API client TypeScript tersanitasi. Halaman mencakup Overview, Market, Analysis, Signals, Risk Management, Trade Plans, Paper Trading, Backtesting, MT5 Connection, Logs, dan Settings.

Frontend hanya memanggil endpoint market/analysis/risk/paper/backtest/MT5 yang sudah tersedia. Tidak ada endpoint atau client method untuk order broker. Riwayat signal global, application log global, dan Telegram belum diekspos backend; dashboard menandai keterbatasan tersebut secara eksplisit dan tidak menyimpan secret di browser. Signals menampilkan latest persisted signal serta history sesi dashboard, sedangkan Logs merangkum status aman MT5, paper, dan backtest.

Testing frontend menggunakan Vitest dan Testing Library untuk routing, API client, state UI, WebSocket reconnect, risk validation, lifecycle paper, emergency confirmation, backtest, CSV, sanitasi secret, responsive navigation, dan batas request execution yang tidak menerima parameter trading bebas.

## Status Milestone 9

Eksekusi broker tersedia hanya dalam mode `MANUAL_DEMO` dan feature flag default `false`. Backend memuat ulang trade plan/signal, memverifikasi signal `CANDIDATE` belum kedaluwarsa, mengulang demo-account guard di dalam lock MT5 tepat sebelum `order_check` dan `order_send`, mengambil fresh Bid/Ask, menghitung ulang volume berbasis risk, memvalidasi spread/stops/freeze/margin, lalu menyimpan request/result tersanitasi dan melakukan rekonsiliasi.

- Engine persisten mendukung `STOPPED`, `STARTING`, `RUNNING`, `PAUSED`, `RISK_LOCKED`, `CONNECTION_LOST`, `ERROR`, dan `EMERGENCY_STOPPED`; startup selalu memaksa `STOPPED`.
- Idempotency, `trade_plan_id`, dan `signal_id` dilindungi unique constraint atomik. Outcome tidak pasti disimpan `UNKNOWN` dan tidak dikirim ulang sebelum reconciliation.
- Maksimal satu retry hanya untuk `REQUOTE` atau `PRICE_CHANGED`. Retcode lain tidak diretry agresif.
- Seluruh `/api/v1/demo/*` membutuhkan `X-Admin-Token` dan rate limit. Token dashboard hanya berada di memori tab, bukan `localStorage`/`sessionStorage`.
- Dashboard menambahkan Demo Trading, execution/order/position/deal history, close, break-even, reconcile, emergency stop, dan tombol `Execute Demo` dengan konfirmasi tepat `EXECUTE DEMO ORDER`.
- Frontend tidak menerima atau mengirim symbol, volume, SL, atau TP bebas untuk execution dari trade plan.
- Tidak ada endpoint akun real dan tidak ada bypass demo guard. Integration test order nyata dipisahkan, opt-in eksplisit, dan tidak dijalankan otomatis.

## Struktur

```text
.
├── backend/
│   ├── app/
│   │   ├── analysis/
│   │   ├── backtest/
│   │   ├── risk/
│   │   ├── paper/
│   │   ├── api/routes/{mt5,market,analysis,risk,paper,backtest}.py
│   │   ├── database/models/
│   │   ├── market_data/
│   │   ├── mt5/
│   │   └── schemas/
│   ├── migrations/
│   └── tests/
├── frontend/
├── .env.example
└── README.md
```

## Prasyarat

- Windows 64-bit
- Python 3.10+ 64-bit dan Node.js 20.19+
- Terminal MetaTrader 5 terpasang dan dapat dibuka oleh user Windows yang menjalankan backend
- Akun **demo** aktif; akun real akan ditolak
- Nginx serta NSSM atau PM2 untuk deployment VPS

## Konfigurasi `.env`

Salin `.env.example` menjadi `.env`, lalu isi kredensial akun demo:

```dotenv
MT5_LOGIN=<DEMO_LOGIN>
MT5_PASSWORD="<DEMO_TRADING_PASSWORD>"
MT5_SERVER=<NAMA_SERVER_DEMO_PERSIS>
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_SYMBOL=XAUUSD

# Tetap false sampai setup admin dan review selesai.
DEMO_EXECUTION_ENABLED=false
DEMO_ADMIN_TOKEN=<TOKEN_ADMIN_ACAK_MINIMAL_16_KARAKTER>
DEMO_EXECUTION_MODE=MANUAL_DEMO
DEMO_MAGIC=9072026
DEMO_COMMENT=bot-demo
DEMO_EMERGENCY_CLOSE_POSITIONS=false
```

`DEMO_EXECUTION_ENABLED` default `false`; `DEMO_EXECUTION_MODE` hanya menerima `MANUAL_DEMO`. Jangan memasukkan token admin ke build Vite. Operator mengetikkannya di halaman Demo Trading dan nilainya hanya disimpan di memori tab.

`MT5_SYMBOL` adalah simbol pilihan broker. Resolver akan mencoba simbol konfigurasi lebih dahulu, kemudian `XAUUSD`, `XAUUSDm`, `XAUUSD.a`, dan `GOLD`. `digits` serta `point` selalu dibaca dari spesifikasi simbol MT5.

Jangan menaruh kredensial di source code, request API, frontend, atau log. `.env.example` wajib tetap kosong dari kredensial nyata.

Parameter strategi menggunakan `ANALYSIS_*`, risk management menggunakan `RISK_*`, dan paper trading menggunakan `PAPER_*`. Settings risk dan paper juga tersimpan di SQLite melalui endpoint masing-masing. Mengubah `PAPER_INITIAL_BALANCE` pada settings baru berlaku setelah paper account di-reset saat engine berhenti dan tidak ada posisi terbuka.

## Instalasi lokal

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm install --prefix frontend
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

## Menjalankan backend

```powershell
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Buka:

- Health API: `http://localhost:8000/api/v1/health`
- Swagger UI: `http://localhost:8000/docs`

Gunakan satu worker Uvicorn karena API MetaTrader 5 memiliki state koneksi process-global.

## Endpoint MT5

| Method | Endpoint                 | Fungsi                             |
| ------ | ------------------------ | ---------------------------------- |
| GET    | `/api/v1/mt5/status`     | Status koneksi                     |
| POST   | `/api/v1/mt5/connect`    | Hubungkan dan validasi akun demo   |
| POST   | `/api/v1/mt5/disconnect` | Putuskan koneksi                   |
| GET    | `/api/v1/mt5/account`    | Informasi akun demo aman           |
| GET    | `/api/v1/mt5/terminal`   | Informasi terminal aman            |
| GET    | `/api/v1/mt5/symbol`     | Spesifikasi simbol yang terdeteksi |

## Endpoint market data

| Method | Endpoint                    | Fungsi                             |
| ------ | --------------------------- | ---------------------------------- |
| GET    | `/api/v1/market/tick`       | Tick, Bid, Ask, dan spread terkini |
| GET    | `/api/v1/market/spread`     | Spread terkini                     |
| GET    | `/api/v1/market/candles`    | Candle OHLCV closed                |
| GET    | `/api/v1/market/timeframes` | Daftar timeframe yang didukung     |
| WS     | `/api/v1/ws/market`         | Stream tick real-time              |

Parameter `/market/candles`:

- `symbol`: opsional; resolver menangani variasi broker.
- `timeframe`: salah satu timeframe yang didukung.
- `count`: 1 sampai `MARKET_MAX_CANDLES`.
- `start_time`: opsional, format ISO 8601 dengan timezone.
- `end_time`: opsional, format ISO 8601 dengan timezone.

Data candle diurutkan ascending berdasarkan waktu, diduplikasi berdasarkan timestamp, dan hanya dikembalikan jika `open_time + durasi_timeframe <= cutoff`. Semua candle response memiliki `is_closed: true`.

## Endpoint analysis

| Method | Endpoint                           | Fungsi                                      |
| ------ | ---------------------------------- | ------------------------------------------- |
| GET    | `/api/v1/analysis/indicators`      | Indikator satu timeframe dari candle closed |
| GET    | `/api/v1/analysis/multi-timeframe` | Snapshot H1, M15, dan M5 dengan satu cutoff |
| POST   | `/api/v1/analysis/signal`          | Hasilkan dan simpan kandidat BUY/SELL/HOLD  |
| GET    | `/api/v1/analysis/latest-signal`   | Ambil sinyal terbaru dari SQLite            |

EMA memakai seed SMA lalu smoothing eksponensial. RSI dan ATR menggunakan smoothing Wilder. H1 menentukan arah trend, M15 memeriksa alignment/crossover EMA dan filter RSI, sedangkan candle M5 mengonfirmasi arah berdasarkan body relatif terhadap ATR dan lokasi close. Seluruh input wajib closed, unik, ascending, lengkap, dan finite.

Skor terdiri dari trend alignment (25), market structure (15), setup alignment (15), RSI (10), confirmation candle (15), spread (10), dan kualitas data (10). Skor hanya mengukur kecocokan aturan strategi dan tidak boleh ditafsirkan sebagai peluang profit.

## Endpoint risk management

| Method | Endpoint                                   | Fungsi                           |
| ------ | ------------------------------------------ | -------------------------------- |
| GET    | `/api/v1/risk/settings`                    | Ambil konfigurasi risiko aktif   |
| PUT    | `/api/v1/risk/settings`                    | Perbarui konfigurasi tervalidasi |
| GET    | `/api/v1/risk/status`                      | Daily risk state dan risk lock   |
| POST   | `/api/v1/risk/trade-plan`                  | Hitung dan simpan trade plan     |
| GET    | `/api/v1/risk/trade-plans`                 | Daftar trade plan                |
| GET    | `/api/v1/risk/trade-plans/{trade_plan_id}` | Detail trade plan                |

`POST /risk/trade-plan` hanya menerima `signal_id` dan optional configuration override. Endpoint ini tidak memiliki jalur order execution.

## Endpoint paper trading

| Method  | Endpoint                             | Fungsi                               |
| ------- | ------------------------------------ | ------------------------------------ |
| GET/PUT | `/api/v1/paper/settings`             | Baca/perbarui konfigurasi paper      |
| GET     | `/api/v1/paper/account`              | Snapshot akun paper                  |
| POST    | `/api/v1/paper/account/reset`        | Reset ledger saat aman               |
| GET     | `/api/v1/paper/status`               | Status engine dan scheduler          |
| POST    | `/api/v1/paper/start`                | Start eksplisit                      |
| POST    | `/api/v1/paper/pause`                | Pause scheduler                      |
| POST    | `/api/v1/paper/stop`                 | Stop engine                          |
| POST    | `/api/v1/paper/emergency-stop`       | Emergency stop                       |
| POST    | `/api/v1/paper/open`                 | Buka posisi dari trade plan approved |
| POST    | `/api/v1/paper/positions/{id}/close` | Tutup posisi paper manual            |
| GET     | `/api/v1/paper/positions[/{id}]`     | Daftar/detail posisi                 |
| GET     | `/api/v1/paper/trades`               | Histori trade closed                 |
| GET     | `/api/v1/paper/statistics`           | Statistik akun                       |
| GET     | `/api/v1/paper/equity-curve`         | Snapshot equity                      |

`POST /paper/open` hanya menerima `trade_plan_id`; lot, SL, dan TP tidak dapat diberikan bebas. Engine status: `STOPPED`, `STARTING`, `RUNNING`, `PAUSED`, `RISK_LOCKED`, `ERROR`, atau `EMERGENCY_STOPPED`.

## Endpoint MT5 demo execution

Semua endpoint berikut membutuhkan header `X-Admin-Token`; mutation juga dibatasi rate limiter backend.

| Method  | Endpoint                                   | Fungsi                                       |
| ------- | ------------------------------------------ | -------------------------------------------- |
| GET/PUT | `/api/v1/demo/settings`                    | Settings aman MANUAL_DEMO                    |
| GET     | `/api/v1/demo/status`                      | Engine dan broker demo status                |
| POST    | `/api/v1/demo/start`                       | Start manual eksplisit                       |
| POST    | `/api/v1/demo/pause`                       | Pause order baru                             |
| POST    | `/api/v1/demo/stop`                        | Stop tanpa auto-close posisi                 |
| POST    | `/api/v1/demo/emergency-stop`              | Emergency stop; close-owned default false    |
| POST    | `/api/v1/demo/execute`                     | Eksekusi APPROVED plan dengan idempotency    |
| GET     | `/api/v1/demo/executions[/{execution_id}]` | Ledger execution tersanitasi                 |
| GET     | `/api/v1/demo/orders`                      | Order demo milik aplikasi                    |
| GET     | `/api/v1/demo/positions`                   | Posisi magic aplikasi                        |
| GET     | `/api/v1/demo/deals`                       | Deal history magic aplikasi                  |
| POST    | `/api/v1/demo/positions/{id}/close`        | Close dengan sisi berlawanan dan fresh quote |
| POST    | `/api/v1/demo/positions/{id}/move-stop`    | Perketat stop yang tervalidasi               |
| POST    | `/api/v1/demo/positions/{id}/break-even`   | Pindahkan stop ke entry saat valid           |
| POST    | `/api/v1/demo/reconcile`                   | Sinkronkan order/position/deal MT5           |

`POST /demo/execute` hanya menerima `trade_plan_id`, `idempotency_key`, dan `confirmation_text` bernilai tepat `EXECUTE DEMO ORDER`. Nilai volume, symbol, SL, dan TP dari frontend ditolak schema.

## Menguji melalui Swagger

1. Pastikan terminal MT5 terbuka dan login ke akun demo.
2. Jalankan backend dan buka `http://localhost:8000/docs`.
3. Jalankan `POST /api/v1/mt5/connect`.
4. Pastikan `connected` dan `demo_verified` bernilai `true`.
5. Jalankan signal dan buat trade plan dari `signal_id`.
6. Pastikan plan `APPROVED`, lalu panggil `POST /api/v1/paper/start`.
7. Kirim hanya `trade_plan_id` ke `POST /api/v1/paper/open`.
8. Pantau account, positions, trades, statistics, dan equity curve.
9. Tutup manual atau biarkan siklus paper memproses SL/TP.
10. Panggil `/paper/stop`, lalu `/mt5/disconnect` setelah selesai.

Swagger tidak menjalankan WebSocket. Gunakan console browser atau klien WebSocket:

```javascript
const socket = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/market");
socket.onmessage = (event) => console.log(JSON.parse(event.data));
```

## Contoh response tick

```json
{
  "symbol": "XAUUSDm",
  "bid": 4012.548,
  "ask": 4012.788,
  "spread_points": 240.0,
  "spread_price": 0.24,
  "timestamp": "2026-07-20T16:21:04.534Z",
  "connection_status": "connected"
}
```

## Contoh response candle

```json
{
  "timestamp": "2026-07-20T16:20:00Z",
  "open": 4012.351,
  "high": 4012.716,
  "low": 4011.739,
  "close": 4012.11,
  "tick_volume": 123,
  "spread": 240,
  "real_volume": 0,
  "is_closed": true
}
```

## Contoh response indikator

```json
{
  "symbol": "XAUUSDm",
  "timeframe": "M15",
  "candle_time": "2026-07-21T12:45:00Z",
  "ema_fast": 3002.15,
  "ema_slow": 2998.42,
  "rsi": 57.31,
  "atr": 3.84,
  "market_structure": "BULLISH",
  "support_levels": [2988.2, 2994.7],
  "resistance_levels": [3008.5],
  "data_valid": true
}
```

## Contoh kandidat sinyal

```json
{
  "signal_id": "<UUID>",
  "symbol": "XAUUSDm",
  "direction": "BUY",
  "strategy_name": "EMA_RSI_ATR_MTF_V1",
  "trend_timeframe": "H1",
  "setup_timeframe": "M15",
  "confirmation_timeframe": "M5",
  "timeframe": "H1/M15/M5",
  "entry_reference_price": 3003.1,
  "atr": 3.84,
  "confidence_score": 100,
  "score_factors": [
    { "factor": "trend_alignment", "passed": true, "weight": 25, "points": 25 },
    {
      "factor": "market_structure",
      "passed": true,
      "weight": 15,
      "points": 15
    },
    { "factor": "setup_alignment", "passed": true, "weight": 15, "points": 15 },
    { "factor": "rsi_filter", "passed": true, "weight": 10, "points": 10 },
    {
      "factor": "candle_confirmation",
      "passed": true,
      "weight": 15,
      "points": 15
    },
    { "factor": "spread_filter", "passed": true, "weight": 10, "points": 10 },
    { "factor": "data_quality", "passed": true, "weight": 10, "points": 10 }
  ],
  "reasons": ["H1 EMA trend is aligned"],
  "rejection_reasons": [],
  "candle_time": "2026-07-21T12:55:00Z",
  "created_at": "2026-07-21T13:00:01Z",
  "status": "CANDIDATE"
}
```

Nilai di atas hanya contoh data uji, bukan harga atau rekomendasi trading aktual. Response nyata menyertakan seluruh faktor skor.

## Rumus position size

Semua input finansial dikonversi melalui `Decimal(str(value))`:

```text
risk_base       = equity (default) atau balance
risk_amount     = risk_base × risk_percent / 100
ticks_at_risk   = stop_distance_price / trade_tick_size
risk_per_lot    = ticks_at_risk × trade_tick_value
raw_lot         = risk_amount / risk_per_lot
normalized_lot  = floor(min(raw_lot, volume_max) / volume_step) × volume_step
```

Lot tidak pernah dibulatkan naik. Hasil di bawah `volume_min`, metadata tick invalid, geometri SL/TP salah, atau risk lock aktif menghasilkan plan `REJECTED`.

## Contoh trade plan test

```json
{
  "trade_plan_id": "<UUID>",
  "signal_id": "signal-buy-candidate",
  "symbol": "XAUUSD",
  "direction": "BUY",
  "entry_price": 3000.2,
  "stop_loss": 2997.2,
  "take_profit": 3006.2,
  "stop_distance_price": 3.0,
  "stop_distance_points": 300.0,
  "risk_percent": 1.0,
  "risk_amount": 100.0,
  "position_size_lots": 0.33,
  "risk_reward": 2.0,
  "spread_points": 20.0,
  "balance": 10000.0,
  "equity": 10000.0,
  "calculation_details": { "source": "MT5 demo read-only snapshot" },
  "validation_reasons": ["Demo account verified", "Risk locks passed"],
  "rejection_reasons": [],
  "status": "APPROVED",
  "created_at": "2026-07-21T12:00:00Z"
}
```

Contoh tersebut adalah data test, bukan rekomendasi atau instruksi transaksi.

## Paper PnL dan contoh lifecycle

```text
BUY gross PnL  = (exit_bid - entry_ask) / tick_size × tick_value × volume
SELL gross PnL = (entry_bid - exit_ask) / tick_size × tick_value × volume
net PnL        = gross PnL - commission + swap
paper equity   = paper balance + floating PnL
```

Contoh BUY test: entry Ask `3000.2`, volume `0.33`, close Take Profit pada Bid `3006.2`, menghasilkan gross paper profit `198.0` sebelum biaya. Contoh SELL loss: entry Bid `3000.0`, close Stop Loss pada Ask `3003.0`, menghasilkan gross paper loss `-99.0` sebelum biaya. Spread sudah tercermin karena entry dan exit memakai sisi quote berbeda.

Contoh statistik setelah satu trade profit:

```json
{
  "total_trades": 1,
  "winning_trades": 1,
  "losing_trades": 0,
  "win_rate": 100.0,
  "gross_profit": 198.0,
  "gross_loss": 0.0,
  "net_profit": 198.0,
  "maximum_drawdown": 0.0,
  "current_balance": 10198.0,
  "current_equity": 10198.0
}
```

Semua angka di bagian ini adalah data simulasi test, bukan transaksi atau hasil akun MT5.

## Menjalankan frontend

```powershell
npm run dev --prefix frontend
```

Dashboard tersedia di `http://localhost:5173`. Build production:

```powershell
npm ci --prefix frontend
npm run build --prefix frontend
```

Hasil build berada di `frontend/dist` dan dilayani Nginx.

## Lint dan test

```powershell
backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m "not integration"
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m integration
npm run lint --prefix frontend
npm run typecheck --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
```

Integration test market/analysis/risk/paper/backtest tetap read-only. Test actual demo order berada di `test_demo_integration.py`, ditandai `integration`, memerlukan opt-in destruktif dan dedicated test magic, serta tidak boleh dijalankan sebelum operator meninjau request minimum-lot tersanitasi dan memberi persetujuan eksplisit.

## Database dan deployment native

SQLite development disimpan di `backend/data/trading_bot.db` dan diabaikan Git. Alembic mengelola signal/risk serta `paper_accounts`, `paper_orders`, `paper_positions`, `paper_trades`, `paper_engine_state`, `paper_settings`, dan `paper_equity_snapshots`. Backend dijalankan langsung dari virtual environment melalui NSSM pada Windows VPS atau PM2 jika sesuai. Nginx melayani `frontend/dist` dan meneruskan REST/WebSocket ke `127.0.0.1:8000`.

Service backend boleh otomatis hidup setelah restart, tetapi koneksi MT5 dan aktivitas bot tidak boleh otomatis dimulai.

## Catatan keamanan

- Trade mode demo diperiksa ulang di backend sebelum operasi MT5 dan tepat sebelum `order_check`/`order_send`; akun real atau contest yang tidak sesuai selalu ditolak.
- Demo execution default disabled, hanya `MANUAL_DEMO`, membutuhkan admin token dan rate limit, serta engine selalu kembali `STOPPED` saat startup.
- Password dan admin token menggunakan `SecretStr`, tidak ada di response schema, dan disanitasi dari error log.
- Execution request/result, order, posisi, deal, event, settings, engine state, dan reconciliation disimpan pada delapan tabel ledger migration `20260725_0006`.
- Dashboard tidak menyimpan admin token, password, atau secret di `localStorage`/`sessionStorage`.
- `.env` diabaikan Git dan tidak boleh disalin ke frontend.
- CORS tetap explicit; autentikasi admin demo tidak menggantikan HTTPS/reverse-proxy hardening saat akses jaringan.
- Dokumentasi API dinonaktifkan saat `APP_ENV=production`.

## Backtesting Milestone 7

### Endpoint

| Method | Endpoint                                       | Fungsi                                                      |
| ------ | ---------------------------------------------- | ----------------------------------------------------------- |
| POST   | `/api/v1/backtests`                            | Validasi konfigurasi dan antrekan background job (HTTP 202) |
| GET    | `/api/v1/backtests`                            | Daftar run                                                  |
| GET    | `/api/v1/backtests/{backtest_id}`              | Status, progress, konfigurasi, dan statistik                |
| POST   | `/api/v1/backtests/{backtest_id}/cancel`       | Cooperative cancellation                                    |
| GET    | `/api/v1/backtests/{backtest_id}/trades`       | Trade hasil simulasi                                        |
| GET    | `/api/v1/backtests/{backtest_id}/equity-curve` | Balance, equity, floating PnL, drawdown                     |
| GET    | `/api/v1/backtests/{backtest_id}/report`       | Laporan lengkap dan warning                                 |
| GET    | `/api/v1/backtests/{backtest_id}/export.csv`   | Export trade CSV                                            |

POST tidak menunggu seluruh simulasi. Pantau `processed_candles`, `total_candles`, `progress_percent`, `current_time`, dan `estimated_remaining_seconds` melalui endpoint detail. Background task hanya dibuat saat run dikirim dan dihentikan secara cooperative pada cancel atau shutdown backend.

### Contoh konfigurasi

```json
{
  "symbol": "XAUUSD",
  "start_date": "2025-01-01",
  "end_date": "2025-06-30",
  "initial_balance": 10000,
  "risk_per_trade_percent": 1,
  "maximum_open_positions": 1,
  "spread_mode": "FIXED",
  "fixed_spread_points": 30,
  "use_historical_spread": false,
  "slippage_points": 0,
  "commission_per_lot": 0,
  "swap_long_per_lot": 0,
  "swap_short_per_lot": 0,
  "minimum_risk_reward": 1.5,
  "trading_sessions": [],
  "strategy_name": "EMA_RSI_ATR_MTF_V1",
  "strategy_settings": {},
  "risk_settings": {},
  "close_open_positions_at_end": true,
  "same_bar_policy": "SL_FIRST",
  "source": "MT5",
  "csv_path": null
}
```

Untuk CSV, gunakan `source: "CSV"` dan isi `csv_path` server. Kolom inti adalah `timestamp,open,high,low,close`; `volume` dan `spread` opsional kecuali `spread_mode` adalah `HISTORICAL`. Timestamp wajib ISO 8601 bertimezone, unik, ascending, dan OHLC harus valid.

### Aturan anti-bias dan asumsi

- Decision dibuat setelah M5 close. H1/M15/M5 yang diberikan ke strategi memiliki `close_time <= decision_time`; window indikator dibatasi oleh `ANALYSIS_CANDLE_COUNT`.
- Sinyal hanya diantrekan pada decision time. Harga open candle berikutnya belum dibaca sampai iterasi candle tersebut dimulai.
- Candle aktif/future dibuang berdasarkan `open_time + duration <= min(end_date, current UTC time)`.
- OHLC historis dianggap harga Bid. Ask adalah Bid ditambah spread. BUY masuk di Ask dan SELL masuk di Bid, lalu adverse slippage diterapkan.
- Jika SL dan TP tersentuh pada candle yang sama, default `SL_FIRST`; `TP_FIRST` harus dipilih eksplisit dan dicatat dalam konfigurasi.
- Duplicate candle ditolak. Gap tidak didedup atau disembunyikan: gap menjadi warning/event laporan.
- Tick size/value, point, volume limits, dan stops level historis tidak tersedia dari MT5. Snapshot spesifikasi simbol akun demo saat run dimulai diterapkan ke seluruh periode dan dicatat sebagai asumsi laporan.
- Strategi, indikator, position sizing, dan PnL mereuse komponen pure mode analysis/risk/paper. Backtest tidak menulis `signals`, `trade_plans`, paper account, atau paper positions.
- Hasil deterministik untuk data dan konfigurasi yang sama, kecuali ID run dan timestamp lifecycle wall-clock.

### Contoh ringkasan hasil

```json
{
  "status": "COMPLETED",
  "initial_balance": 10000,
  "final_balance": 10042.5,
  "net_profit": 42.5,
  "total_return_percent": 0.425,
  "total_trades": 4,
  "winning_trades": 2,
  "losing_trades": 2,
  "win_rate": 50,
  "gross_profit": 180,
  "gross_loss": 137.5,
  "profit_factor": 1.3091,
  "expectancy": 10.625,
  "average_win": 90,
  "average_loss": -68.75,
  "maximum_drawdown": 137.5,
  "maximum_drawdown_percent": 1.36,
  "consecutive_wins": 1,
  "consecutive_losses": 1,
  "average_risk_reward": 2,
  "sharpe_ratio": 0.18
}
```

Angka tersebut hanya ilustrasi format, bukan hasil akun atau rekomendasi trading. Run dengan kurang dari 30 trade diberi warning. Laporan selalu menyatakan bahwa performa masa lalu tidak menjamin hasil masa depan.

Contoh equity curve:

```json
[
  {
    "timestamp": "2025-01-02T10:05:00Z",
    "balance": 10000,
    "equity": 10000,
    "floating_pnl": 0,
    "drawdown": 0
  },
  {
    "timestamp": "2025-01-02T10:10:00Z",
    "balance": 10000,
    "equity": 9992.5,
    "floating_pnl": -7.5,
    "drawdown": 7.5
  }
]
```

Contoh CSV:

```csv
trade_id,direction,entry_time,exit_time,entry_price,exit_price,stop_loss,take_profit,volume,gross_profit_loss,commission,swap,net_profit_loss,close_reason,signal_id,trade_plan_id
<trade-id>,BUY,2025-01-02T10:05:00+00:00,2025-01-02T11:20:00+00:00,2640.30,2643.30,2638.80,2643.30,0.10,30.00,0.00,0.00,30.00,TAKE_PROFIT,<signal-id>,<plan-id>
```

### Database dan verifikasi

Alembic menambah tepat tujuh tabel: `backtests`, `backtest_settings`, `backtest_trades`, `backtest_positions`, `backtest_equity_snapshots`, `backtest_events`, dan `backtest_reports`.

```powershell
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests backend\migrations
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m "not integration"
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m integration
npm run lint --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

Melalui Swagger: hubungkan MT5 demo dengan `/mt5/connect`, kirim konfigurasi ke `/backtests`, poll detail sampai terminal, lalu baca report/equity/CSV. Paper engine dan demo execution engine tidak perlu dijalankan; subsystem backtest tetap read-only dan tidak memanggil API pengiriman order.
