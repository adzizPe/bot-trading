from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "frontend" / "nginx.conf"
SNIPPETS = ROOT / "frontend" / "nginx" / "snippets"
CONFIG = CONFIG_PATH.read_text(encoding="utf-8")
SECURITY = (SNIPPETS / "security-headers.conf").read_text(encoding="utf-8")
PROXY = (SNIPPETS / "proxy-common.conf").read_text(encoding="utf-8")
WEBSOCKET = (SNIPPETS / "proxy-websocket.conf").read_text(encoding="utf-8")
BROTLI = (SNIPPETS / "brotli.conf.example").read_text(encoding="utf-8")
BENCHMARK = (ROOT / "scripts" / "Benchmark-Nginx.ps1").read_text(encoding="utf-8")
ACTIVE_CONFIG = "\n".join(
    line for line in CONFIG.splitlines()
    if line.strip() and not line.lstrip().startswith("#")
)


def block_from(start: int) -> str:
    opening = CONFIG.index("{", start)
    depth = 0
    for index in range(opening, len(CONFIG)):
        if CONFIG[index] == "{":
            depth += 1
        elif CONFIG[index] == "}":
            depth -= 1
            if depth == 0:
                return CONFIG[start:index + 1]
    raise AssertionError(f"Unclosed block at offset {start}")


def block(marker: str) -> str:
    return block_from(CONFIG.index(marker))


def test_configuration_lint_contract() -> None:
    assert CONFIG.count("{") == CONFIG.count("}")
    assert "server_name _;" not in CONFIG
    assert "proxy_pass http://127.0.0.1:8000" not in CONFIG
    assert "upstream trading_backend" in CONFIG
    assert "server 127.0.0.1:8000;" in CONFIG
    assert "include mime.types;" in CONFIG
    assert "include snippets/security-headers.conf;" in CONFIG
    assert not re.search(r"ssl_protocols[^;]*(?:SSLv|TLSv1(?:\.0|\.1)?)(?:\s|;)", CONFIG)


def test_https_redirect_hsts_ocsp_and_modern_tls() -> None:
    assert "listen 80 default_server;" in CONFIG
    assert "return 308 https://trading.example.com$request_uri;" in CONFIG
    assert "listen 443 ssl default_server;" in CONFIG
    assert "listen [::]:443 ssl default_server;" in CONFIG
    assert "if ($host != trading.example.com) { return 444; }" in CONFIG
    assert "ssl_certificate certs/trading.example.com/fullchain.pem;" in CONFIG
    assert "ssl_certificate_key certs/trading.example.com/privkey.pem;" in CONFIG
    assert "ssl_trusted_certificate certs/trading.example.com/chain.pem;" in CONFIG
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in CONFIG
    assert "ECDHE-RSA-AES128-GCM-SHA256" in CONFIG
    assert "ssl_session_tickets off;" in CONFIG
    assert "ssl_stapling on;" in CONFIG
    assert "ssl_stapling_verify on;" in CONFIG
    assert "resolver_timeout 5s;" in CONFIG
    assert "Strict-Transport-Security" in SECURITY
    assert "max-age=31536000; includeSubDomains" in SECURITY


def test_security_headers_are_fail_closed_and_cross_origin_aware() -> None:
    expected = {
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
        "Cross-Origin-Embedder-Policy",
    }
    assert all(f"add_header {header}" in SECURITY for header in expected)
    assert "frame-ancestors 'none'" in SECURITY
    assert "object-src 'none'" in SECURITY
    assert "script-src 'self'" in SECURITY
    assert "X-Frame-Options \"DENY\" always;" in SECURITY
    assert "X-Content-Type-Options \"nosniff\" always;" in SECURITY
    assert "Cross-Origin-Embedder-Policy \"require-corp\"" in SECURITY


def test_static_cache_and_compression_contract() -> None:
    assets = block("location /assets/")
    index = block("location = /index.html")
    assert "location ^~ /assets/" not in CONFIG
    assert "location ~* \\.(?:map|md|env|ini|log|sql)$" in CONFIG
    assert "max-age=31536000, immutable" in assets
    assert "try_files $uri =404;" in assets
    assert "no-store, no-cache, must-revalidate" in index
    assert "add_header Expires \"0\" always;" in index
    assert "gzip on;" in CONFIG
    assert "gzip_vary on;" in CONFIG
    assert "gzip_types" in CONFIG and "application/json" in CONFIG
    assert "include snippets/brotli.conf;" in CONFIG
    assert "include snippets/brotli.conf;" not in ACTIVE_CONFIG
    assert "brotli on;" in BROTLI
    assert "nginx -V" in BROTLI


def test_request_connection_rate_and_large_upload_limits() -> None:
    assert "limit_req_zone $binary_remote_addr zone=api_per_ip:10m rate=20r/s;" in CONFIG
    assert "zone=auth_per_ip:10m rate=10r/m;" in CONFIG
    assert "zone=upload_per_ip:10m rate=2r/m;" in CONFIG
    assert "limit_conn_zone $binary_remote_addr zone=conn_per_ip:10m;" in CONFIG
    assert "limit_req_status 429;" in CONFIG
    assert "limit_conn_status 429;" in CONFIG
    assert "client_max_body_size 1m;" in CONFIG

    upload = block("location = /api/v1/backtests/uploads")
    assert "client_max_body_size 52m;" in upload
    assert "client_body_timeout 120s;" in upload
    assert "limit_req zone=upload_per_ip burst=2 nodelay;" in upload
    assert "limit_conn conn_per_ip 2;" in upload
    assert "proxy_connect_timeout 5s;" in upload
    assert "proxy_send_timeout 120s;" in upload
    assert "proxy_read_timeout 120s;" in upload
    assert "proxy_request_buffering on;" in upload


def test_websocket_exact_and_prefix_proxy_are_unbuffered_and_bounded() -> None:
    exact = block("location = /api/v1/ws")
    prefix = block("location ^~ /api/v1/ws/")
    for location in (exact, prefix):
        assert "limit_req zone=ws_per_ip burst=10 nodelay;" in location
        assert "limit_conn ws_conn_per_ip 20;" in location
        assert "include snippets/proxy-websocket.conf;" in location
        assert "proxy_pass http://trading_backend;" in location
        assert "logs/websocket-access.log" in location
    assert "proxy_set_header Upgrade $http_upgrade;" in WEBSOCKET
    assert "proxy_set_header Connection $connection_upgrade;" in WEBSOCKET
    assert "proxy_buffering off;" in WEBSOCKET
    assert "proxy_request_buffering off;" in WEBSOCKET
    assert "proxy_read_timeout 300s;" in WEBSOCKET
    assert "proxy_send_timeout 300s;" in WEBSOCKET
    assert "proxy_socket_keepalive on;" in WEBSOCKET


def test_proxy_timeouts_and_trusted_forwarding_contract() -> None:
    assert "proxy_connect_timeout 5s;" in PROXY
    assert "proxy_read_timeout 120s;" in PROXY
    assert "proxy_send_timeout 120s;" in PROXY
    assert "proxy_buffering on;" in PROXY
    for snippet in (PROXY, WEBSOCKET):
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in snippet
        assert "$proxy_add_x_forwarded_for" not in snippet
        assert "proxy_set_header X-Forwarded-Proto $scheme;" in snippet
        assert "proxy_set_header X-Request-ID $request_id;" in snippet


def test_status_is_local_only_and_logs_are_separated() -> None:
    assert CONFIG.count("location = /nginx/status") == 2
    for match in re.finditer(r"location = /nginx/status", CONFIG):
        status_block = block_from(match.start())
        assert "stub_status;" in status_block
        assert "allow 127.0.0.1;" in status_block
        assert "allow ::1;" in status_block
        assert "deny all;" in status_block
        assert "access_log off;" in status_block
    assert "access_log logs/access.log main" in CONFIG
    assert "error_log logs/error.log warn;" in CONFIG
    assert "Cookie" not in CONFIG
    assert "Authorization" not in CONFIG


def test_operational_scripts_exist_for_config_test_rotation_and_benchmark() -> None:
    scripts = ROOT / "scripts"
    assert (scripts / "Test-NginxConfig.ps1").is_file()
    assert (scripts / "Rotate-NginxLogs.ps1").is_file()
    assert (scripts / "Benchmark-Nginx.ps1").is_file()
    assert "[ValidateSet('/healthz')]" in BENCHMARK
    assert "Add-Type -AssemblyName System.Net.Http" in BENCHMARK
    assert "catch {" in BENCHMARK
    assert "BaseUri must use HTTPS" in BENCHMARK
    assert (ROOT / "docs" / "deployment" / "windows-nginx.md").is_file()


def test_authoritative_readiness_is_exact_and_healthz_remains_edge_only() -> None:
    readiness = block("location = /api/v1/health/readiness")
    edge_health = block("location = /healthz")
    assert CONFIG.count("location = /api/v1/health/readiness") == 1
    assert "zone=readiness_per_ip:10m rate=60r/m;" in CONFIG
    assert "include snippets/proxy-common.conf;" in readiness
    assert "proxy_pass http://trading_backend;" in readiness
    assert "proxy_connect_timeout 5s;" in readiness
    assert "proxy_send_timeout 5s;" in readiness
    assert "proxy_read_timeout 5s;" in readiness
    assert 'add_header Cache-Control "no-store" always;' in readiness
    assert "proxy_pass" not in edge_health
    assert 'return 200 "ok\\n";' in edge_health
