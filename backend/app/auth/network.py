from ipaddress import ip_address, ip_network

from starlette.requests import HTTPConnection


def source_ip(request: HTTPConnection, trusted_proxies: list[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    if not _trusted(peer, trusted_proxies):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or "," in forwarded:
        return peer
    candidate = forwarded.strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return peer


def _trusted(peer: str, trusted_proxies: list[str]) -> bool:
    try:
        address = ip_address(peer)
    except ValueError:
        return False
    for value in trusted_proxies:
        try:
            if address in ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False
