# XAU/USD Trading Bot

Aplikasi web untuk pembelajaran, analisis, risk planning, dan simulasi paper trading XAU/USD menggunakan harga akun demo MetaTrader 5. **Milestone 6 hanya membuat transaksi di database aplikasi.** Tidak ada pengiriman order, pembukaan/penutupan posisi MT5, atau dukungan akun real.

## Status Milestone 6

Tersedia:

- Seluruh market data, signal analysis, dan risk planning Milestone 1–5
- Paper account dengan balance, equity, margin simulasi, floating dan realized PnL
- Paper order, position, trade history, dan equity snapshots di SQLite
- Entry BUY pada Ask dan SELL pada Bid; exit BUY pada Bid dan SELL pada Ask
- Spread real-time, slippage, commission, dan swap yang dapat dikonfigurasi
- Stop-loss/take-profit monitoring, manual close, dan emergency close
- Break-even dan trailing-stop modular dengan audit log perubahan SL
- Manual opening hanya dari trade plan `APPROVED`
- Auto mode opt-in dengan dedup signal/trade plan dan batas posisi/risk lock
- Scheduler explicit-start; engine selalu tidak aktif saat backend baru hidup
- Statistik performa dan equity curve
- Deployment native: Python virtual environment, Vite build, dan Nginx

Belum tersedia:

- Order execution MT5 atau posisi asli pada akun demo
- Akun real, backtesting, atau frontend dashboard lengkap
- Deployment milestone lanjutan

Backend tidak otomatis terhubung ke MT5 dan paper engine tidak otomatis `RUNNING`. Koneksi MT5 dan `POST /api/v1/paper/start` harus dipanggil secara eksplisit. Seluruh hasil paper tetap berada di SQLite.

## Struktur

```text
.
├── backend/
│   ├── app/
│   │   ├── analysis/
│   │   ├── risk/
│   │   ├── paper/
│   │   ├── api/routes/{mt5,market,analysis,risk,paper}.py
│   │   ├── config/
│   │   ├── database/models/
│   │   ├── market_data/
│   │   ├── mt5/
│   │   └── schemas/
│   ├── migrations/
│   ├── tests/
│   ├── alembic.ini
│   ├── pytest.ini
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/
│   ├── nginx.conf
│   └── package.json
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
```

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
npm run build --prefix frontend
```

Integration test memverifikasi akun demo, market data, analysis, risk planning, lifecycle paper explicit-start, quote real-time, endpoint/OpenAPI, dan disconnect. Adapter/protocol MT5 tetap read-only dan tidak menyediakan pengiriman order.

## Database dan deployment native

SQLite development disimpan di `backend/data/trading_bot.db` dan diabaikan Git. Alembic mengelola signal/risk serta `paper_accounts`, `paper_orders`, `paper_positions`, `paper_trades`, `paper_engine_state`, `paper_settings`, dan `paper_equity_snapshots`. Backend dijalankan langsung dari virtual environment melalui NSSM pada Windows VPS atau PM2 jika sesuai. Nginx melayani `frontend/dist` dan meneruskan REST/WebSocket ke `127.0.0.1:8000`.

Service backend boleh otomatis hidup setelah restart, tetapi koneksi MT5 dan aktivitas bot tidak boleh otomatis dimulai.

## Catatan keamanan

- Trade mode demo diperiksa ulang sebelum setiap pembacaan market data, termasuk cache hit.
- Password menggunakan `SecretStr`, tidak ada di response schema, dan disanitasi dari error log.
- `.env` diabaikan Git dan tidak boleh disalin ke frontend.
- API saat ini untuk akses lokal/development; authentication dan HTTPS wajib ditambahkan sebelum akses publik.
- Dokumentasi API dinonaktifkan saat `APP_ENV=production`.
