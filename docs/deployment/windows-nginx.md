# Native Windows Nginx production runbook

Milestone 10.5 tetap memakai deployment native: satu Uvicorn worker pada `127.0.0.1:8000`, Vite build di `frontend/dist`, Nginx Windows sebagai TLS reverse proxy, dan NSSM/PM2 sebagai process manager. Tidak ada container, service queue, atau upstream tambahan.

## 1. Prasyarat dan placeholder

Gunakan build Nginx Windows tepercaya yang mendukung OpenSSL modern, TLS 1.2/1.3, `stub_status`, `limit_req`, `limit_conn`, gzip, dan OCSP stapling. Template `frontend/nginx.conf` adalah full top-level config, bukan hanya `server` include.

Sebelum digunakan, ganti seluruh nilai berikut:

- `trading.example.com` dengan hostname production yang tervalidasi DNS.
- `C:/apps/xauusd-trading-bot/frontend/dist` bila release root berbeda.
- `conf/certs/trading.example.com/fullchain.pem`, `privkey.pem`, dan `chain.pem` dengan certificate chain yang benar.
- Resolver OCSP hanya bila kebijakan jaringan VPS tidak mengizinkan resolver template.

Jangan menyimpan private key di repository. Batasi ACL `privkey.pem` ke akun Windows yang menjalankan Nginx dan administrator. HSTS template mencakup subdomain; pastikan semua subdomain sudah HTTPS sebelum go-live.

## 2. Instalasi konfigurasi

Backup konfigurasi aktif, lalu salin template dan snippets ke prefix native Nginx:

```powershell
$stamp = Get-Date -Format yyyyMMdd-HHmmss
Copy-Item C:\nginx\conf\nginx.conf "C:\nginx\conf\nginx.conf.$stamp.bak"
Copy-Item frontend\nginx.conf C:\nginx\conf\nginx.conf
New-Item -ItemType Directory -Force C:\nginx\conf\snippets | Out-Null
Copy-Item frontend\nginx\snippets\security-headers.conf C:\nginx\conf\snippets\
Copy-Item frontend\nginx\snippets\proxy-common.conf C:\nginx\conf\snippets\
Copy-Item frontend\nginx\snippets\proxy-websocket.conf C:\nginx\conf\snippets\
```

Edit hanya placeholder deployment. Backend wajib tetap bind ke loopback dan berjalan dengan tepat satu worker. Set production `AUTH_TRUSTED_PROXIES` ke loopback peer Nginx yang benar dan `AUTH_COOKIE_SECURE=true`; jangan membuka port 8000 pada firewall publik.

## 3. Configuration test dan reload

Selalu uji konfigurasi sebelum start atau graceful reload:

```powershell
.\scripts\Test-NginxConfig.ps1 -NginxRoot C:\nginx
C:\nginx\nginx.exe -p C:\nginx\ -s reload
```

Script test hanya menjalankan `nginx -t`; script tidak start, stop, reload, atau deploy. Jika test gagal, jangan reload. Periksa certificate path, include path, dukungan modul, syntax, dan log error.

## 4. TLS, OCSP, dan header

Port 80 hanya melayani local-only `/nginx/status`; request lain mendapat permanent redirect `308` ke hostname HTTPS tetap. TLS server hanya mengaktifkan TLS 1.2/1.3, cipher ECDHE modern, session cache tanpa session ticket, HSTS, dan OCSP stapling verification.

OCSP membutuhkan `fullchain.pem` dan issuer chain yang benar pada `ssl_trusted_certificate`, DNS resolver yang dapat dijangkau worker Nginx, serta certificate yang menyediakan responder OCSP. Warning stapling pada `error.log` harus diselesaikan sebelum rollout; jangan mematikan verification hanya untuk menghilangkan warning.

Header production meliputi CSP, frame denial, MIME sniffing denial, referrer policy, permissions policy, COOP, CORP, dan COEP. CSP mengizinkan inline style karena komponen dashboard/chart existing memakainya, tetapi script tetap hanya dari origin yang sama. Setelah build, smoke-test seluruh chart, login, CSV download, dan WebSocket pada browser production.

## 5. Request, upload, dan timeout

Default body edge adalah 1 MiB. Hanya `POST /api/v1/backtests/uploads` mendapat 52 MiB, yaitu default backend `MAX_CSV_SIZE_MB=50` ditambah multipart overhead. Jika limit backend berubah, review Nginx bersamaan; jangan menaikkan edge ke maksimum 500 MiB tanpa capacity review disk/temp, bandwidth, dan timeout.

Rate limit dibagi menjadi API, login, upload, dan WebSocket handshake. Limit koneksi HTTP dan WebSocket juga terpisah. HTTP `429` digunakan untuk edge rejection. Backend authentication, lockout, authorization, upload validation, dan WebSocket limits tetap authoritative; edge limit adalah lapisan tambahan.

API menggunakan connect timeout 5 detik dan read/send timeout 120 detik. WebSocket menggunakan buffering off dan read/send timeout 300 detik, lebih panjang dari heartbeat/idle backend. Upload memiliki body/read/send timeout 120 detik. Jangan menaikkan timeout tanpa bounded backend operation.

## 6. Compression dan cache

Gzip aktif untuk text, CSS, JavaScript, SVG, XML, manifest, dan JSON dengan `Vary: Accept-Encoding`. Asset Vite hashed di `/assets/` mendapat cache satu tahun dan `immutable`; `index.html` selalu `no-store, no-cache, must-revalidate` agar release baru ditemukan.

Brotli tidak tersedia pada build Nginx standar tertentu dan directive yang tidak dikenal membuat configuration test gagal. Aktifkan hanya jika build yang dipasang menunjukkan modul Brotli yang kompatibel:

```powershell
C:\nginx\nginx.exe -V 2>&1 | Select-String -Pattern brotli
Copy-Item frontend\nginx\snippets\brotli.conf.example C:\nginx\conf\snippets\brotli.conf
# Uncomment include conf/snippets/brotli.conf; in C:\nginx\conf\nginx.conf
.\scripts\Test-NginxConfig.ps1 -NginxRoot C:\nginx
```

Tanpa modul tersebut, biarkan include Brotli tetap dikomentari; gzip tetap aktif.

## 7. WebSocket dan monitoring

Kedua route berikut harus dipertahankan karena backend memakai keduanya:

- Exact `/api/v1/ws` untuk generic topic protocol.
- Prefix `/api/v1/ws/` untuk `/api/v1/ws/market` dan route private berikutnya.

Keduanya meneruskan Upgrade/Connection, mematikan proxy buffering/request buffering, memakai socket keepalive, dan menulis `logs/websocket-access.log`. Jangan meneruskan X-Forwarded-For dari client; template overwrite dengan `$remote_addr` agar trust boundary auth konsisten.

`/nginx/status` memakai `stub_status`, `allow 127.0.0.1`, `allow ::1`, lalu `deny all`. Akses hanya dari host VPS:

```powershell
curl.exe --fail http://127.0.0.1/nginx/status
```

Jangan expose endpoint ini melalui firewall, load balancer, atau DNS publik.

## 8. Smoke test keamanan

Jalankan setelah certificate dan hostname aktif, sebelum mengalihkan traffic:

```powershell
curl.exe -I http://trading.example.com/
curl.exe -I https://trading.example.com/
curl.exe --compressed -I https://trading.example.com/assets/<HASHED-ASSET>.js
curl.exe -I https://trading.example.com/index.html
curl.exe -I https://trading.example.com/openapi.json
```

Verifikasi redirect HTTP `308`, HSTS/CSP/security headers pada HTTPS, `immutable` pada hashed asset, `no-store` pada index, compression saat payload memenuhi ukuran minimum, dan HTTP `404` untuk docs/OpenAPI. Uji WebSocket melalui dashboard dan pastikan exact generic topic serta market stream dapat handshake tanpa polling per client.

Large-upload test harus memakai CSV synthetic tanpa data akun dan ukuran di bawah 50 MiB untuk success path; payload di atas 52 MiB harus ditolak Nginx dengan `413`. Rate/timeout tests dilakukan dari localhost atau staging terisolasi agar tidak memengaruhi user dan tidak mengirim order MT5.

Benchmark read-only dapat dijalankan terhadap `/healthz` pada host yang sudah terkonfigurasi:

```powershell
.\scripts\Benchmark-Nginx.ps1 -BaseUri https://trading.example.com -Requests 10000 -Concurrency 50
```

Script menolak HTTP dan tidak menonaktifkan certificate validation. Jangan benchmark endpoint mutation, login, upload, atau broker.

## 9. Log rotation

Access, WebSocket access, dan error log dipisah. Jadwalkan `scripts/Rotate-NginxLogs.ps1` melalui Windows Task Scheduler menggunakan akun yang memiliki akses ke prefix Nginx. Default retention 30 hari. Script menjalankan configuration test, memindahkan hanya tiga nama log yang dikenal, mengirim `nginx -s reopen`, lalu menghapus hanya archive yang cocok pola dan melewati retention.

Uji manual dengan `-WhatIf` sebelum membuat task:

```powershell
.\scripts\Rotate-NginxLogs.ps1 -NginxRoot C:\nginx -RetentionDays 30 -WhatIf
```

Pantau kegagalan task dan kapasitas disk. Jangan log cookie, Authorization header, request body, token, atau credential.

## 10. Update, backup, rollback, dan recovery

Sebelum update, backup secara terenkripsi dan access-controlled:

- `C:/nginx/conf/nginx.conf` dan seluruh snippets.
- Certificate chain dan private key secara terpisah dengan ACL ketat.
- Release `frontend/dist` aktif.
- Referensi versi Nginx dan modul yang dipasang.

Alur update native:

1. Jalankan test backend/frontend dan `npm run build --prefix frontend`.
2. Siapkan salinan `frontend/dist` baru di direktori staging lokal.
3. Backup dist/config aktif dengan timestamp.
4. Salin dist baru, kemudian jalankan `Test-NginxConfig.ps1`.
5. Graceful reload Nginx dan jalankan smoke test HTTPS/cache/WebSocket.
6. Jangan connect MT5 atau mengaktifkan demo execution sebagai bagian update web.

Jika smoke test gagal, kembalikan dist dan config backup, jalankan `nginx -t`, lalu graceful reload. Jika certificate rusak/expired, pulihkan chain/key backup yang valid atau hasil renewal, uji config, reload, dan verifikasi OCSP/HSTS. Jika Nginx gagal start, jangan expose Uvicorn; pertahankan port 8000 loopback, baca `logs/error.log`, dan rollback konfigurasi terakhir yang tervalidasi.
