from typing import Any


RETCODE_NAMES = {
    10004: "REQUOTE", 10006: "REJECT", 10007: "CANCEL", 10008: "PLACED",
    10009: "DONE", 10010: "DONE_PARTIAL", 10011: "ERROR", 10012: "TIMEOUT",
    10013: "INVALID", 10014: "INVALID_VOLUME", 10015: "INVALID_PRICE",
    10016: "INVALID_STOPS", 10017: "TRADE_DISABLED", 10018: "MARKET_CLOSED",
    10019: "NO_MONEY", 10020: "PRICE_CHANGED", 10021: "PRICE_OFF",
    10022: "INVALID_EXPIRATION", 10023: "ORDER_CHANGED", 10024: "TOO_MANY_REQUESTS",
    10025: "NO_CHANGES", 10026: "SERVER_DISABLES_AT", 10027: "CLIENT_DISABLES_AT",
    10028: "LOCKED", 10029: "FROZEN", 10030: "INVALID_FILL",
    10031: "CONNECTION", 10032: "ONLY_REAL", 10033: "LIMIT_ORDERS",
    10034: "LIMIT_VOLUME", 10035: "INVALID_ORDER", 10036: "POSITION_CLOSED",
    10038: "INVALID_CLOSE_VOLUME", 10039: "CLOSE_ORDER_EXIST",
    10040: "LIMIT_POSITIONS", 10041: "REJECT_CANCEL", 10042: "LONG_ONLY",
    10043: "SHORT_ONLY", 10044: "CLOSE_ONLY", 10045: "FIFO_CLOSE",
    10046: "HEDGE_PROHIBITED",
}


def retcode_name(retcode: int | None) -> str:
    return RETCODE_NAMES.get(retcode, "UNKNOWN_RETCODE")


def sanitize_vendor_result(result: object, value: Any) -> dict[str, Any]:
    retcode = int(value(result, "retcode", -1))
    return {"retcode": retcode, "retcode_name": retcode_name(retcode)}


class OrderCheckService:
    """Safe retcode facade used by the MT5 order-check/send boundary."""

    retcode_name = staticmethod(retcode_name)
    sanitize_vendor_result = staticmethod(sanitize_vendor_result)
