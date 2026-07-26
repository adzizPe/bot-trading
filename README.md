# XAU/USD Trading Bot

Aplikasi pembelajaran untuk market data, analisis, risk planning, paper trading, backtesting, dan eksekusi manual XAU/USD pada akun **demo** MetaTrader 5. Backend tetap menolak akun real/contest yang tidak sesuai, tidak mengaktifkan auto trading, dan tidak pernah memulai engine demo otomatis setelah restart.

## Status Milestone 10.1 — Authentication dan RBAC

Autentikasi terpusat menggantikan header admin lama. Browser memakai access/refresh token melalui cookie `HttpOnly`, `SameSite=Strict`, dan `Secure` pada production; token tidak boleh disimpan di `localStorage` atau `sessionStorage`. Cookie `csrf_token` sengaja dapat dibaca frontend untuk pola double-submit, wajib dikirim sebagai `X-CSRF-Token` pada mutation berbasis cookie, dan hash-nya terikat ke sesi aktif serta dirotasi bersama refresh token. Access token default berlaku 900 detik, refresh token 604800 detik dan dirotasi saat refresh. Login dibatasi per source IP (default 10 kegagalan/300 detik), sedangkan akun dikunci sementara setelah 5 kegagalan selama 900 detik.

Route data publik hanya `GET /api/v1/health`. `POST /api/v1/auth/login` dan `POST /api/v1/auth/refresh` tidak membutuhkan access token karena merupakan endpoint protokol autentikasi, bukan route data publik. Semua REST route `/api/v1/*` lain serta WebSocket `/api/v1/ws/market` membutuhkan sesi valid dan permission eksplisit. UI dokumentasi dan OpenAPI tidak boleh diekspos; production menjalankan `APP_ENV=production` untuk menonaktifkan `/docs`, dan Nginx menolak `/docs`, `/redoc`, serta `/openapi.json`.

### Matriks permission backend

Permission memakai nama colon berikut; endpoint read-only dalam satu router tetap membutuhkan permission dasar router tersebut.

| Route/operasi                                                     | Permission minimum                                        |
| ----------------------------------------------------------------- | --------------------------------------------------------- |
| `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`                 | Sesi valid; tanpa permission colon tambahan               |
| `GET /api/v1/market/*`, `WS /api/v1/ws/market`                    | `market:read`                                             |
| `GET /api/v1/analysis/*`                                          | `signals:read`                                            |
| `POST /api/v1/analysis/signal`                                    | `signals:read` + `analysis:generate`                      |
| `GET /api/v1/mt5/*`                                               | `dashboard:read`                                          |
| `POST /api/v1/mt5/connect`, `POST /api/v1/mt5/disconnect`         | `dashboard:read` + `mt5:control`                          |
| Read-only `/api/v1/risk/*`                                        | `dashboard:read`                                          |
| `PUT /api/v1/risk/settings`                                       | `dashboard:read` + `risk:settings:update`                 |
| `GET /api/v1/risk/feasibility`                                    | `dashboard:read` + `risk:feasibility`                     |
| `POST /api/v1/risk/trade-plan`                                    | `dashboard:read` + `trade-plan:create`                    |
| Read-only `/api/v1/paper/*`                                       | `dashboard:read`                                          |
| Paper settings/reset/start/pause/stop/emergency-stop              | `dashboard:read` + `paper:control`                        |
| Paper open/close                                                  | `dashboard:read` + `paper:trade`                          |
| Read-only `/api/v1/backtests/*`                                   | `statistics:read`                                         |
| Submit/cancel backtest                                            | `statistics:read` + `backtest:submit` / `backtest:cancel` |
| Read-only `/api/v1/demo/*`                                        | `dashboard:read`                                          |
| Demo start/pause/stop/execute                                     | `dashboard:read` + `demo:execute`                         |
| Demo close/move-stop/break-even/trailing/cancel-pending/reconcile | `dashboard:read` + `demo:position:manage`                 |
| `PUT /api/v1/demo/settings`                                       | `dashboard:read` + `demo:settings:update`                 |
| Demo/safety emergency stop                                        | `dashboard:read` + `emergency-stop:execute`               |
| Safety status/events dan `GET /api/v1/health/full`                | `dashboard:read`                                          |
| Safety emergency/circuit reset                                    | `dashboard:read` + `safety:reset`                         |
| Auth user create/list                                             | `users:manage`                                            |
| Auth role update                                                  | `roles:manage`                                            |
| Auth session list/invalidate                                      | `sessions:invalidate`                                     |

Role bawaan: `VIEWER` memiliki `dashboard:read`, `market:read`, `signals:read`, dan `statistics:read`; `OPERATOR` menambah kontrol MT5, analysis, paper, serta submit/cancel backtest; `RISK_ADMIN` menambah update risk, create trade plan, dan feasibility; `EXECUTION_ADMIN` menambah demo execute, demo position management, dan emergency stop di atas permission read; `SUPER_ADMIN` memiliki seluruh permission termasuk administrasi user/role/session, demo settings, dan safety reset.

### Bootstrap satu kali

Jalankan migration native Alembic sampai revision `20260728_0009`, lalu buat super-admin pertama secara interaktif satu kali. Command meminta username, password minimal 12 karakter, dan konfirmasi; tidak ada username atau password default dan password tidak menjadi argumen command/history.

```powershell
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
Push-Location backend
.\.venv\Scripts\python.exe -m app.auth.bootstrap
Pop-Location
```

Setelah bootstrap, login melalui dashboard, buat akun operator sesuai least privilege, dan jangan menjalankan bootstrap kembali kecuali memang perlu membuat `SUPER_ADMIN` tambahan secara eksplisit.

## Status Milestone 10.2 — Backtest Resource Management

Backend backtest memakai coordinator FIFO bounded native dengan default satu worker dan tiga slot pending. Admission memvalidasi symbol, timeframe M5, rentang tanggal, estimasi candle, metadata upload, serta reservasi memori agregat seluruh job sebelum membuat row; kapasitas penuh menghasilkan HTTP `429` tanpa row baru. Timestamp creation dibuat monoton agar recovery FIFO tetap deterministik pada Windows. Timeout menghasilkan `FAILED/JOB_TIMEOUT`; shutdown/restart merekam alasan stabil dan startup memulihkan `PENDING` secara FIFO. Pembatalan job pending langsung membebaskan slot queue, progress SQLite dibatch, ledger/report/event/equity dibatasi, dan export CSV memakai existence check ringan serta pembacaan repository berhalaman.

CSV tidak lagi menerima path filesystem dari request. Upload lebih dahulu melalui `POST /api/v1/backtests/uploads` sebagai multipart `.csv` UTF-8, lalu kirim `csv_upload_id` pada `POST /api/v1/backtests`; ID bersifat single-use selama dimiliki job dan dilarang untuk source `MT5`. Staging memakai nama UUID di `backend/data/backtest_uploads`, memvalidasi MIME/header/timestamp timezone/OHLC/ukuran/baris serta overlap rentang request, tidak mengekspos path, dan dibersihkan saat reject, cancel, terminal, timeout, shutdown, atau orphan cleanup termasuk file data tanpa metadata. Upload/submit tetap membutuhkan `statistics:read` + `backtest:submit`; queue/resources/limits tetap read-only dengan `statistics:read`.

Batas efektif dikonfigurasi dengan `MAX_BACKTEST_JOBS`, `MAX_PENDING_JOBS`, `MAX_CANDLES`, `MAX_DATE_RANGE_DAYS`, `MAX_CSV_SIZE_MB`, `MAX_CSV_ROWS`, `MAX_MEMORY_BUDGET_MB`, dan `JOB_TIMEOUT_MINUTES`. Deployment tetap native Python virtual environment/FastAPI/Uvicorn di belakang Nginx dan NSSM/PM2, tanpa Docker atau service queue eksternal. Jalankan **tepat satu worker Uvicorn** agar coordinator in-process, recovery SQLite, dan state MT5 process-global memiliki satu owner.

## Status Milestone 10.3 — MT5 Connector Isolation & Timeout Hardening

Seluruh vendor I/O MT5 berada di belakang satu connector berserial dengan hard deadline. Pada runtime produksi `MetaTrader5Client`, connector memakai satu child process Windows `spawn`; hanya child tersebut yang memanggil API vendor. Timeout atau crash menghentikan generation process, mengarantina connector, menolak broker mutation baru, dan menjalankan bounded recovery/reconnect. Backend tetap responsif karena status dan metrics dibaca dari snapshot parent tanpa menunggu vendor.

State connector adalah `CONNECTED`, `DISCONNECTED`, `DEGRADED`, `TIMEOUT`, `RECOVERING`, atau `FAILED`. Timeout `order_send`/close tidak pernah langsung di-retry: outcome menjadi `UNKNOWN`, mutation gate tetap tertutup, dan gate hanya dibuka setelah snapshot reconciliation berhasil membaca position, active/history order, deal, serta history. Explicit broker `TIMEOUT`/connection retcode mengikuti gate UNKNOWN yang sama. Existing demo account guard, idempotency, final safety guard, dan permission backend tetap authoritative.

Heartbeat MT5 dedicated berjalan dari lifecycle connector dan melakukan bounded native `terminal_info`, bukan hanya membaca flag lama. Metrics connector mencatat jumlah call, latency average/last/max dan per operation, timeout, failure, retry, reconnect, serta generation. Konfigurasi native: `MT5_VENDOR_TIMEOUT_MS`, `MT5_ORDER_SEND_TIMEOUT_MS`, `MT5_HEARTBEAT_TIMEOUT_MS`, `MT5_HEARTBEAT_INTERVAL_SECONDS`, `MT5_RECOVERY_RETRIES`, dan `MT5_RECOVERY_DELAY_SECONDS`; connect tetap memakai `MT5_TIMEOUT_MS`, `MT5_CONNECT_RETRIES`, dan `MT5_RETRY_DELAY_SECONDS`. Child connector bukan service deployment terpisah dan tidak menambah Docker/container, port, broker, atau queue eksternal. Tetap gunakan tepat satu worker Uvicorn.

## Status Milestone 10.4 — WebSocket Hardening

WebSocket private memakai access cookie `HttpOnly` atau bearer header untuk non-browser client; token tidak pernah diterima melalui URL. Handshake memvalidasi token, sesi, expiry, origin yang dikirim client, dan permission backend. Sesi aktif divalidasi ulang berkala dan koneksi ditutup dengan `4401` saat token kedaluwarsa/revoked atau `4403` saat permission tidak lagi cukup. Browser merespons heartbeat aplikasi dan tidak melakukan reconnect loop untuk close auth/permission.

Satu `WebSocketHub` in-process dimiliki lifespan aplikasi. Publisher market membaca `MarketDataService` paling banyak sekali per interval untuk setiap simbol aktif, memperbarui cache tick/Bid/Ask/spread/status, lalu melakukan fan-out ke seluruh subscriber; jumlah pembacaan tidak bertambah bersama jumlah client. `/api/v1/ws/market` tetap kompatibel dengan payload tick mentah, tetapi query `interval_seconds` tidak lagi dapat mengubah cadence server. `/api/v1/ws` menyediakan channel read-only `market`, `analysis`, `signals`, `paper`, `backtest`, `logs`, dan `health` melalui frame `{"type":"subscribe","topics":[...]}`. Hanya channel market memiliki publisher aktif; channel lain adalah bus internal dan tidak melakukan polling atau mutation domain.

Setiap client memiliki queue bounded dengan kebijakan drop-oldest/latest-wins dan ditutup `1013` setelah melewati batas slow-client. Hub membatasi koneksi per user, per source IP, dan total; juga menerapkan idle/heartbeat timeout serta rate limit handshake, reconnect, dan subscribe. Snapshot agregat read-only tersedia di `GET /api/v1/websocket/status` dengan permission `market:read`, mencakup active connection per topic, reconnect, rejected connection, dropped message, delivery latency, broadcast duration, cache market, dan frekuensi market read. Tidak ada token, cookie, username, session ID, atau IP yang diekspos.

Konfigurasi native memakai `WS_MAX_CONNECTIONS_PER_USER`, `WS_MAX_CONNECTIONS_PER_IP`, `WS_MAX_TOTAL_CONNECTIONS`, `WS_IDLE_TIMEOUT_SECONDS`, `WS_HEARTBEAT_INTERVAL_SECONDS`, `WS_HEARTBEAT_TIMEOUT_SECONDS`, `WS_CLIENT_BUFFER_SIZE`, `WS_SLOW_CLIENT_DROP_LIMIT`, `WS_SEND_TIMEOUT_SECONDS`, `WS_HANDSHAKE_RATE_LIMIT`, `WS_HANDSHAKE_RATE_WINDOW_SECONDS`, `WS_RECONNECT_RATE_LIMIT`, `WS_RECONNECT_RATE_WINDOW_SECONDS`, `WS_SUBSCRIBE_RATE_LIMIT`, `WS_SUBSCRIBE_RATE_WINDOW_SECONDS`, dan `WS_SESSION_REVALIDATE_SECONDS`. Jalankan benchmark offline dari folder `backend` dengan `.\.venv\Scripts\python.exe -m benchmarks.websocket_fanout`. Deployment tetap native venv/Uvicorn + Vite dist + Nginx + NSSM/PM2 dan wajib satu worker; milestone ini tidak mengubah konfigurasi Nginx, menambah container, service eksternal, atau queue eksternal.

## Status Milestone 10.5 — Nginx Production Hardening

`frontend/nginx.conf` sekarang merupakan template full native Windows Nginx dengan HTTP-to-HTTPS `308`, TLS 1.2/1.3, cipher ECDHE modern, HSTS, OCSP stapling verification, session hardening, CSP dan security/cross-origin headers. Domain `trading.example.com`, certificate paths, dan release root adalah placeholder deployment yang wajib diganti lalu divalidasi menggunakan `scripts/Test-NginxConfig.ps1`; certificate/private key tidak disimpan di repository.

Edge menerapkan rate dan connection limit terpisah untuk API, login, upload, serta WebSocket; body default dibatasi 1 MiB dan upload CSV default 52 MiB agar menampung backend `MAX_CSV_SIZE_MB=50` plus multipart overhead. API, upload, client-body, dan WebSocket memiliki timeout eksplisit. Exact `/api/v1/ws` dan prefix `/api/v1/ws/` sama-sama memperoleh Upgrade header, buffering off, socket keepalive, dan log WebSocket terpisah. Forwarded source IP selalu dioverwrite dengan peer `$remote_addr`, bukan mempercayai header client.

Gzip aktif. Brotli tersedia sebagai snippet opt-in dan hanya boleh di-include setelah `nginx -V` membuktikan modul tersedia serta `nginx -t` lulus. Asset Vite hashed menggunakan cache satu tahun `immutable`, sedangkan `index.html` memakai `no-store`. `/nginx/status` menggunakan `stub_status` dan hanya mengizinkan loopback. Access/error/WebSocket log dipisah; script rotation, HTTPS benchmark read-only, serta runbook setup/update/backup/rollback/recovery tersedia di `docs/deployment/windows-nginx.md`. Deployment tetap native dan satu worker Uvicorn; tidak ada container atau deployment otomatis.

## Status Milestone 10.7 — SQLite Backup and Recovery Readiness

Recovery SQLite operator-side tersedia melalui wrapper native Windows PowerShell 5.1 untuk backup online, verifikasi, copy off-host terverifikasi, GFS retention, restore offline/dry-run, drill terisolasi, dan status. Target default adalah RPO 24 jam, RTO 2 jam, interval 24 jam, serta retention 7 daily/4 weekly/3 monthly. Setiap direktori backup memiliki `manifest.json` sebagai **source of truth**; `status.json` hanya cache tersanitasi yang dapat dibangun ulang.

Command aman dari repository root:

```powershell
.\scripts\Backup-Database.ps1
.\scripts\Verify-Backup.ps1 -BackupId '<backup-uuid>'
.\scripts\Copy-BackupOffHost.ps1 -BackupId '<backup-uuid>'
.\scripts\Invoke-BackupRetention.ps1 -DryRun
.\scripts\Get-BackupStatus.ps1
.\scripts\Restore-Database.ps1 -BackupId '<backup-uuid>' -DryRun
.\scripts\Invoke-RestoreDrill.ps1
```

Restore dry-run tetap membutuhkan backend/writer offline dan menjalankan autentikasi/dekripsi, checksum, integrity, compatibility/migration pada candidate, serta repository smoke check, tetapi tidak membuat forensic copy final dan tidak mengubah active DB/WAL/SHM. Normal restore tidak dijadwalkan dan hanya boleh dijalankan manual setelah dry-run lulus; jangan restart backend/demo/MT5 setelah kegagalan atau setelah restore sampai seluruh post-check dan sign-off lulus. Raw copy active database bukan backup yang valid.

`Get-BackupStatus.ps1` melaporkan availability, waktu backup/verifikasi terakhir, age dan `rpo_met`, status/waktu off-host, jadwal berikutnya, hasil/durasi drill dan `rto_met`, serta failure category terbaru. Batasan: key 32-byte base64 dan destination tidak memiliki default aman, key tidak boleh berada di argv/repository/script, restore wajib offline dan berbasis backup ID, penghapusan temporary plaintext hanya best effort sehingga ACL ketat dan encrypted volume wajib, dan operasi tidak mengendalikan lifecycle service. Deployment tetap native dan wajib **tepat satu worker Uvicorn**. Prosedur harian, Task Scheduler, incident, key lifecycle, forensic DB/WAL/SHM, restore, drill, dan sign-off ada di [runbook SQLite recovery Windows](docs/deployment/windows-sqlite-recovery.md).

## Status Milestone 10.8 — Native Windows Service Operations Runbook

Primary runbook operasional tersedia di [docs/deployment/windows-service-operations.md](docs/deployment/windows-service-operations.md). Topology canonical memakai NSSM; PM2 hanya alternatif mutually exclusive yang memerlukan review setara. Backend tetap exact satu worker Uvicorn dari venv pada `127.0.0.1:8000`, Vite `frontend/dist` dilayani Nginx, dan Nginx adalah satu-satunya edge publik. Static `/healthz` hanya Edge Liveness; release gate memerlukan Backend Readiness exact `/api/v1/health/readiness` melalui loopback dan proxy Nginx.

Semua wrapper operations repository adalah offline `PLAN`/`WhatIf` secara default dan tidak membuktikan production telah dikonfigurasi. Eksekusi memerlukan reviewed host adapter terpisah dan deployment/change approval; repository tidak menyediakan adapter production. Runbook mencakup setup/lifecycle/reboot/update/rollback/failure/Windows Update, certificate/capacity/log/monitoring/hardening/secret/ACL, Restore Hold handoff tanpa mengubah semantics Milestone 10.7, disaster recovery, Operator Evidence Package dengan retention minimal 180 hari dan two-person sign-off, serta isolated drill setiap 90 hari. Semua lifecycle wajib Trading-Safe: MT5 disconnected, Demo/Paper stopped, dan zero broker mutation.

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

- Engine persisten mendukung `STOPPED`, `STARTING`, `RUNNING`, `PAUSED`, `RISK_LOCKED`, `CONNECTION_LOST`, `ERROR`, dan `EMERGENCY_STOP`; startup selalu memaksa `STOPPED`.
- Idempotency, `trade_plan_id`, dan `signal_id` dilindungi unique constraint atomik. Outcome tidak pasti disimpan `UNKNOWN` dan tidak dikirim ulang sebelum reconciliation.
- Maksimal satu retry hanya untuk `REQUOTE` atau `PRICE_CHANGED`. Retcode lain tidak diretry agresif.
- Seluruh `/api/v1/demo/*` memakai autentikasi terpusat, permission colon yang sesuai operasi, CSRF untuk mutation berbasis cookie, dan rate limit backend. Access/refresh token dashboard hanya berada di cookie—bukan `localStorage`/`sessionStorage`.
- Dashboard menambahkan Demo Trading, execution/order/position/deal history, close, break-even, reconcile, emergency stop, dan tombol `Execute Demo` dengan konfirmasi tepat `EXECUTE DEMO ORDER`.
- Frontend tidak menerima atau mengirim symbol, volume, SL, atau TP bebas untuk execution dari trade plan.
- Tidak ada endpoint akun real dan tidak ada bypass demo guard. Integration test order nyata dipisahkan, opt-in eksplisit, dan tidak dijalankan otomatis.

## Status Milestone 9.5 — Safety Layer

Safety Layer berada sebelum executor dan memiliki final synchronous kill-switch tepat sebelum vendor `order_send`. `EmergencyStopManager`, connection/spread/daily-loss/drawdown/weekend/session/news/duplicate guardians, heartbeat, health monitor, circuit breaker, dan audit trail bekerja fail-closed tanpa mengubah Strategy Engine, Risk Management, Paper Trading, atau Backtesting.

- Emergency aktif memblokir seluruh mutation trading dengan HTTP `423 Locked`, mengubah engine menjadi `EMERGENCY_STOP`, dan tidak menjalankan auto-close karena semua vendor send harus tetap nol.
- Connection guardian memblokir MT5 disconnected, `terminal_trade_allowed=false`, atau `terminal_api_disabled=true`; spread, daily loss, drawdown, weekend, sesi, news, dan duplicate plan juga memblokir trading.
- Heartbeat memeriksa database, MT5, backend, dan WebSocket setiap 5 detik. Status `DEGRADED`/`UNHEALTHY` memblokir trading.
- Circuit breaker terbuka setelah 5 infrastructure error dalam window 30 menit dan mengunci trading selama 30 menit.
- Sesi `LONDON`, `NEW_YORK`, `ASIA`, dan `CUSTOM` memakai database IANA timezone (`tzdata`) agar DST ditangani konsisten pada Windows.
- Dashboard Demo Trading hanya ditambah panel Safety Status, Guardian Status, Circuit Breaker, Heartbeat, Health, dan Emergency Stop; kontrol existing tidak dirombak.
- Event emergency, reject/block, guardian, session, weekend, connection, spread, dan daily-loss disimpan pada audit trail safety.

## Status Milestone 9.6 — Risk Feasibility Analyzer

Risk Feasibility Analyzer telah tersedia sebagai diagnosis **read-only, advisory-only** sebelum pembuatan Trade Plan. Analyzer menjawab apakah formula position sizing existing dapat menghasilkan volume yang memenuhi grid lot broker berdasarkan candidate signal, risk settings aktif, serta snapshot account/tick/symbol yang fresh. Hasilnya bukan approval, rejection gate, rekomendasi trading, atau pengganti validasi authoritative pada flow Trade Plan.

### Endpoint dan kontrak

```http
GET /api/v1/risk/feasibility?signal_id=<SIGNAL_ID>
Cache-Control: no-store
```

- Request query wajib berisi tepat satu `signal_id`; tidak ada request body. Query yang hilang, duplikat, atau menambahkan override seperti equity, balance, risk percent, entry, stop loss, volume, tick value, maupun symbol specification ditolak dengan HTTP `422`.
- Signal yang tidak ditemukan menghasilkan HTTP `404` tersanitasi. Hasil domain `FEASIBLE`, `INFEASIBLE`, dan `UNAVAILABLE` menggunakan kontrak response yang sama; `POST` pada endpoint ini tidak didukung.
- `FEASIBLE` berarti floor-normalized lot memenuhi effective minimum broker lot, tetapi final Trade Plan masih dapat ditolak oleh flow authoritative existing. `INFEASIBLE` berarti input valid tetapi configured risk tidak menghasilkan lot executable dan recommendation-nya `DO_NOT_FORCE_MINIMUM_LOT`. `UNAVAILABLE` berarti input/snapshot tidak lengkap, invalid, stale, atau tidak konsisten dan recommendation-nya `RETRY_WITH_VALID_FRESH_DATA`.
- Nilai presisi keputusan diserialisasi sebagai **decimal strings** (atau `null` bila diagnostic tidak aman dihitung), bukan JSON binary float. Response menyertakan unit currency, percent, lot, price, point, dan tick-derived.
- Snapshot account, tick, dan symbol specification dibaca atomik melalui jalur MT5 read-only. Response menyertakan `captured_at`, `account_at`, `symbol_at`, `tick_at`, dan `fresh_until`; tick yang melewati freshness policy 60 detik menghasilkan `UNAVAILABLE/SNAPSHOT_STALE`. Backend dan frontend sama-sama memakai `no-store`, dan dashboard membuang hasil yang expired, berasal dari signal lain, atau kalah oleh request yang lebih baru.

### Diagnostic position sizing

Analyzer menampilkan `raw_lot` sebagai nilai diagnostic non-executable, `capped_lot`, `normalized_lot` yang selalu di-floor ke zero-origin `volume_step` tanpa round-up/clamp-up, configured `volume_min`, effective `minimum_broker_lot`, `volume_max`, dan `volume_step`. Status volume ditentukan dari perbandingan normalized lot dengan effective minimum broker lot; minimum lot tidak pernah dipaksakan.

Diagnostic threshold mencakup:

- `required_minimum_risk_base`, yaitu modal minimum matematis agar minimum broker lot sesuai configured risk. Jika risk base memakai equity, `required_minimum_equity` bernilai sama dan applicable; jika risk base memakai balance, threshold tetap bertipe `BALANCE` dan required equity ditandai hypothetical/not applicable.
- `maximum_stop_distance`, versi points, dan direction-aware `boundary_stop_loss_price`: batas SL advisory yang masih memungkinkan minimum broker lot tanpa menaikkan risk. Boundary ini tidak mengubah stop loss signal atau Trade Plan.
- Estimasi amount/percent risiko jika minimum broker lot digunakan, beserta delta terhadap configured risk, selalu berlabel `DIAGNOSTIC_ONLY`. Nilai ini hanya menjelaskan konsekuensi hipotetis—bukan instruksi untuk force, override, round-up, create plan, atau execute order.

### Zero mutation dan compatibility

Analysis Result hanya hidup selama request/cache UI sementara: **zero persistence**, tanpa tabel, migration, backfill, atau record feasibility. Pemanggilan berulang tidak menulis Trade Plan, `daily_risk_state`, risk settings/state, signal, order, position, deal, safety event, maupun state broker; response juga tidak memiliki `trade_plan_id`. Analyzer tidak memanggil `order_check`, `order_send`, paper/demo executor, atau operasi broker mutation apa pun.

Strategy dan signal generation/persistence tidak berubah. Risk Management—settings, limits, lock, counters, formula, serta persistence—tidak berubah. Source, interface, rounding, normalization, output, dan exception `PositionSizeCalculator` tidak berubah. Trade Plan flow, approval/rejection, records, list/detail contract, dan tombol create existing tidak berubah atau bergantung pada status analyzer. Paper Trading, Backtesting, Demo Execution, Safety Layer, dan Order Executor beserta order/reconciliation/position management juga tidak berubah.

Dashboard menambahkan route **Risk Feasibility Analyzer** (`/risk-feasibility`) untuk latest candidate signal. Halaman menampilkan status dengan teks/icon, raw/floor-normalized/minimum lot, required minimum equity/risk base, maximum stop distance/boundary SL, diagnostic-only minimum-lot risk, reason codes, recommendation, dan advisory disclaimer. Tidak ada editable calculation fields maupun tombol force/override/create/execute; state loading/error/unavailable/stale tidak pernah ditampilkan sebagai feasible.

Focused test commands:

```powershell
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests\test_risk_feasibility.py backend\tests\test_risk_feasibility_service.py backend\tests\test_risk_feasibility_routes.py backend\tests\test_risk_feasibility_integration.py backend\tests\test_risk_feasibility_properties.py
npm run test --prefix frontend -- src/riskFeasibility.test.ts src/pages/RiskFeasibilityPage.test.tsx src/api/client.test.ts
npm run typecheck --prefix frontend
```

Deployment tidak berubah: backend tetap dijalankan langsung dari native Python virtual environment dengan FastAPI/Uvicorn, frontend tetap Vite build ke `frontend/dist` dan dilayani Nginx, serta process management tetap NSSM pada Windows VPS atau PM2 bila sesuai. Milestone 9.6 tidak menambah Docker/container, service/daemon/queue baru, port/upstream baru, atau konfigurasi `.env` baru.

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

Salin `.env.example` menjadi `.env`, lalu ganti seluruh placeholder MT5 dengan kredensial **akun demo** milik operator. Jangan commit `.env`.

```dotenv
APP_ENV=development
MT5_LOGIN=<DEMO_ACCOUNT_LOGIN>
MT5_PASSWORD=<DEMO_ACCOUNT_PASSWORD>
MT5_SERVER=<EXACT_DEMO_SERVER_NAME>
MT5_PATH=C:\Path\To\MetaTrader 5\terminal64.exe
MT5_SYMBOL=XAUUSD

# Local HTTP only. Production HTTPS wajib true.
AUTH_ACCESS_TTL_SECONDS=900
AUTH_REFRESH_TTL_SECONDS=604800
AUTH_COOKIE_SECURE=false
AUTH_TRUSTED_PROXIES=[]
AUTH_LOGIN_RATE_LIMIT=10
AUTH_LOGIN_RATE_WINDOW_SECONDS=300
AUTH_ACCOUNT_LOCKOUT_ATTEMPTS=5
AUTH_ACCOUNT_LOCKOUT_SECONDS=900

# Tetap false sampai setup admin dan review selesai.
DEMO_EXECUTION_ENABLED=false
DEMO_EXECUTION_MODE=MANUAL_DEMO
DEMO_MAGIC=9072026
DEMO_COMMENT=bot-demo
DEMO_EMERGENCY_CLOSE_POSITIONS=false
```

`AUTH_COOKIE_SECURE=false` hanya untuk local HTTP (`localhost`). Production wajib memakai HTTPS dan `AUTH_COOKIE_SECURE=true`; konfigurasi production dengan cookie tidak secure ditolak backend. `AUTH_TRUSTED_PROXIES` adalah JSON array CIDR/IP proxy yang benar-benar dipercaya, misalnya `["127.0.0.1/32","::1/128"]` bila Nginx lokal menjadi satu-satunya peer backend. Jangan memakai trust-all. Backend hanya membaca `X-Forwarded-For` saat peer langsung cocok dengan daftar tersebut.

`DEMO_EXECUTION_ENABLED` default `false`; `DEMO_EXECUTION_MODE` hanya menerima `MANUAL_DEMO`. Akses demo dikendalikan oleh sesi terautentikasi dan permission RBAC, bukan secret yang dimasukkan ke dashboard atau build Vite.

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

Buka health check publik di `http://localhost:8000/api/v1/health`. Endpoint aplikasi lainnya membutuhkan login dan permission; dokumentasi interaktif/OpenAPI tidak digunakan sebagai alur operasi.

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

Seluruh endpoint berikut membutuhkan sesi valid dan permission sesuai matriks Milestone 10.1; mutation juga memakai CSRF untuk autentikasi cookie dan dibatasi rate limiter backend.

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

## Endpoint Safety Layer

Endpoint safety membutuhkan sesi valid dan permission sesuai matriks Milestone 10.1. `/health/full` bersifat read-only tetapi tetap protected dengan `dashboard:read`; hanya `/health` yang menjadi health route publik.

| Method | Endpoint                          | Fungsi                                              |
| ------ | --------------------------------- | --------------------------------------------------- |
| GET    | `/api/v1/safety/status`           | Status seluruh guardian dan trading allowed         |
| POST   | `/api/v1/safety/emergency-stop`   | Aktifkan hard emergency stop                        |
| POST   | `/api/v1/safety/emergency-reset`  | Reset emergency secara eksplisit                    |
| POST   | `/api/v1/safety/circuit-reset`    | Reset circuit breaker secara eksplisit              |
| GET    | `/api/v1/safety/events?limit=100` | Audit event safety tersanitasi                      |
| GET    | `/api/v1/health/full`             | Health database/MT5/backend/WebSocket dan subsystem |

`/health/full` mengembalikan status database, MT5, market, risk, paper, backtest, frontend, versi, build, uptime, heartbeat, dan snapshot safety. Reset tidak mengaktifkan engine atau auto trading; operator tetap harus memulai alur demo secara manual.

## Alur operasi terautentikasi

1. Pastikan terminal MT5 terbuka dan login ke akun demo.
2. Login melalui dashboard; browser mengirim cookie dengan `credentials: include` dan `X-CSRF-Token` untuk mutation.
3. Operator dengan `mt5:control` menjalankan koneksi MT5 dan memastikan `connected` serta `demo_verified` bernilai `true`.
4. Role dengan permission yang tepat membuat signal/trade plan, lalu menjalankan paper atau demo secara eksplisit.
5. Pantau account, positions, trades, statistics, dan equity curve dari dashboard.
6. Stop engine dan disconnect MT5 setelah selesai.

WebSocket market memakai access cookie yang sama dan memerlukan `market:read`. Jangan menaruh access/refresh token pada URL, JavaScript storage, source code, log, atau konfigurasi Vite.

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
backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests backend\migrations
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m "not integration"
backend\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend\tests -m safety_integration
npm run lint --prefix frontend
npm run typecheck --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
```

Marker `safety_integration` sepenuhnya offline: fake MT5 dan SQLite temporary, dengan assertion `order_send_calls == 0`. Integration test actual demo order berada di `test_demo_integration.py`, memerlukan marker/opt-in destruktif serta persetujuan operator terpisah, dan tidak termasuk validasi Safety Layer normal.

## Database dan deployment native VPS

SQLite development disimpan di `backend/data/trading_bot.db` dan diabaikan Git. Jalankan Alembic native sampai migration backtest resource management `20260728_0009` (di atas auth/RBAC `20260727_0008`, safety `20260726_0007`, dan ledger demo `20260725_0006`) sebelum bootstrap user. Backend berjalan langsung dari Python virtual environment melalui FastAPI/Uvicorn dan NSSM pada Windows VPS atau PM2 bila sesuai. Frontend di-build oleh Vite ke `frontend/dist` lalu dilayani Nginx; Nginx menjadi reverse proxy REST dan WebSocket ke Uvicorn `127.0.0.1:8000`. Ini adalah satu-satunya flow deployment project.

Backend harus tetap bind ke loopback agar client tidak dapat melewati Nginx. Canonical production template berada di `frontend/nginx.conf`; runbook native lengkap berada di `docs/deployment/windows-nginx.md`. Pada setiap blok proxy, template **overwrite** header forwarding dari koneksi Nginx—jangan meneruskan nilai `X-Forwarded-For` yang diberikan client. Template juga mencakup kedua route WebSocket yang berbeda: exact `/api/v1/ws` dan prefix `/api/v1/ws/`, serta edge denial untuk `/docs`, `/redoc`, dan `/openapi.json`.

Set `AUTH_TRUSTED_PROXIES` hanya ke IP/CIDR peer Nginx yang eksplisit; untuk Nginx pada host yang sama gunakan loopback yang sesuai konfigurasi listen, bukan subnet luas. Production wajib `APP_ENV=production`, HTTPS, dan `AUTH_COOKIE_SECURE=true`. Service backend boleh otomatis hidup setelah restart, tetapi koneksi MT5 dan aktivitas bot tidak boleh otomatis dimulai.

## Catatan keamanan

- Trade mode demo diperiksa ulang di backend sebelum operasi MT5 dan tepat sebelum `order_check`/`order_send`; akun real atau contest yang tidak sesuai selalu ditolak.
- Demo execution default disabled, hanya `MANUAL_DEMO`, membutuhkan sesi/RBAC serta rate limit, dan engine selalu kembali `STOPPED` saat startup.
- Access/refresh token berada pada cookie `HttpOnly`, `SameSite=Strict`; `Secure` wajib pada production. CSRF memakai cookie/header double-submit untuk mutation berbasis cookie.
- Access/refresh token tidak disimpan di `localStorage` atau `sessionStorage`; storage browser hanya boleh berisi preferensi tampilan non-rahasia.
- Password dan token disimpan/ditangani secara tersanitasi; jangan taruh credential dalam response, source, log, build frontend, atau command bootstrap.
- `.env` diabaikan Git dan tidak boleh disalin ke frontend. `.env.example` hanya berisi placeholder aman.
- Login memiliki rate limit per source IP dan temporary account lockout; akurasi source IP bergantung pada overwrite XFF oleh Nginx dan `AUTH_TRUSTED_PROXIES` yang minimal.
- CORS tetap explicit. Production tidak mengekspos docs/OpenAPI dan tidak menerima direct access ke Uvicorn.

## Backtesting Milestone 10.2

### Endpoint

| Method | Endpoint                                       | Fungsi                                                      |
| ------ | ---------------------------------------------- | ----------------------------------------------------------- |
| POST   | `/api/v1/backtests/uploads`                    | Stage dan validasi multipart CSV (HTTP 201)                 |
| POST   | `/api/v1/backtests`                            | Validasi konfigurasi dan antrekan background job (HTTP 202) |
| GET    | `/api/v1/backtests`                            | Daftar run                                                  |
| GET    | `/api/v1/backtests/queue`                      | ID/count/capacity antrean FIFO                              |
| GET    | `/api/v1/backtests/resources`                  | Estimasi candle, memori, dan staged bytes                   |
| GET    | `/api/v1/backtests/limits`                     | Batas efektif coordinator                                   |
| GET    | `/api/v1/backtests/{backtest_id}`              | Status, progress, konfigurasi, dan statistik                |
| POST   | `/api/v1/backtests/{backtest_id}/cancel`       | Cooperative cancellation                                    |
| GET    | `/api/v1/backtests/{backtest_id}/trades`       | Trade hasil simulasi                                        |
| GET    | `/api/v1/backtests/{backtest_id}/equity-curve` | Balance, equity, floating PnL, drawdown                     |
| GET    | `/api/v1/backtests/{backtest_id}/report`       | Laporan lengkap dan warning                                 |
| GET    | `/api/v1/backtests/{backtest_id}/export.csv`   | Export trade CSV                                            |

POST tidak menunggu seluruh simulasi. Pantau `processed_candles`, `total_candles`, `progress_percent`, `current_time`, dan `estimated_remaining_seconds` melalui endpoint detail. Coordinator FIFO menjalankan jumlah worker/slot pending yang dibatasi konfigurasi, melakukan recovery saat startup, dan merekam terminal reason stabil pada timeout/shutdown/restart.

### Contoh konfigurasi

```json
{
  "symbol": "XAUUSD",
  "start_date": "2025-01-01",
  "end_date": "2025-06-30",
  "timeframe": "M5",
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
  "source": "MT5"
}
```

Untuk CSV, upload multipart `.csv` terlebih dahulu ke `/api/v1/backtests/uploads`, lalu gunakan `source: "CSV"` dan `csv_upload_id` UUID yang dikembalikan. Caller tidak dapat mengirim path filesystem server. Kolom inti adalah `timestamp,open,high,low,close`; `volume`, `tick_volume`, dan `spread` opsional kecuali `spread_mode` adalah `HISTORICAL`. Timestamp wajib ISO 8601 bertimezone, unik, ascending, dan OHLC harus valid.

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

Melalui dashboard terautentikasi: hubungkan MT5 demo, kirim konfigurasi backtest, poll detail sampai terminal, lalu baca report/equity/CSV. Permission minimum mengikuti matriks Milestone 10.1. Paper engine dan demo execution engine tidak perlu dijalankan; subsystem backtest tetap read-only terhadap broker dan tidak memanggil API pengiriman order.
