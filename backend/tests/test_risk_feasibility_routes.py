from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from app.risk_feasibility.mapper import RiskFeasibilityResultMapper
from app.risk_feasibility.service import FeasibilitySignalNotFoundError
from app.risk_feasibility.types import ReasonCode, unavailable_result
from tests.auth_helpers import authenticated_client
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings
from tests.test_risk_feasibility import calculate, raw_input

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


class FakeFeasibilityService:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def analyze(self, signal_id: str) -> dict[str, Any]:
        self.calls.append(signal_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def app_client(result: dict[str, Any] | Exception) -> tuple[TestClient, FakeFeasibilityService]:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    service = FakeFeasibilityService(result)
    app = create_app(
        settings,
        manager,
        risk_feasibility_service=service,  # type: ignore[arg-type]
    )
    return authenticated_client(app), service


def unavailable() -> dict[str, Any]:
    return unavailable_result(
        signal_id="signal-1", symbol="XAUUSD", direction="BUY",
        now=NOW, code=ReasonCode.SNAPSHOT_UNAVAILABLE,
    )


def test_get_contract_is_query_only_non_cacheable_and_complete() -> None:
    api, service = app_client(unavailable())
    with api:
        response = api.get("/api/v1/risk/feasibility?signal_id=signal-1")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["status"] == "UNAVAILABLE"
        assert body["advisory"] is True
        assert body["volume"]["raw_lot"] is None
        assert "trade_plan_id" not in response.text
        assert service.calls == ["signal-1"]
        assert api.post(
            "/api/v1/risk/feasibility", json={"signal_id": "signal-1"}
        ).status_code == 405


def test_extra_or_missing_query_parameters_are_rejected_without_service_call() -> None:
    api, service = app_client(unavailable())
    with api:
        extra = api.get(
            "/api/v1/risk/feasibility?signal_id=signal-1&equity=999999"
        )
        missing = api.get("/api/v1/risk/feasibility")
        duplicate = api.get(
            "/api/v1/risk/feasibility?signal_id=signal-1&signal_id=signal-2"
        )
        assert extra.status_code == 422
        assert missing.status_code == 422
        assert duplicate.status_code == 422
        assert extra.headers["cache-control"] == "no-store"
        assert missing.headers["cache-control"] == "no-store"
        assert service.calls == []


def test_not_found_and_unexpected_errors_are_sanitized() -> None:
    api, _ = app_client(FeasibilitySignalNotFoundError("secret login=123"))
    with api:
        missing = api.get("/api/v1/risk/feasibility?signal_id=missing")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Signal was not found"}
        assert "secret" not in missing.text
    api, _ = app_client(RuntimeError("Authorization: bearer secret"))
    with api:
        failed = api.get("/api/v1/risk/feasibility?signal_id=signal-1")
        assert failed.status_code == 500
        assert failed.json() == {"detail": "Risk feasibility analysis failed"}
        assert "bearer" not in failed.text


def test_openapi_exposes_only_get_signal_id_contract() -> None:
    api, _ = app_client(unavailable())
    with api:
        operation = api.app.openapi()["paths"][  # type: ignore[attr-defined]
            "/api/v1/risk/feasibility"
        ]
        assert set(operation) == {"get"}
        parameters = operation["get"]["parameters"]
        assert [(item["name"], item["in"]) for item in parameters] == [
            ("signal_id", "query")
        ]


@pytest.mark.parametrize(
    ("risk_base", "expected"), [("10000", "FEASIBLE"), ("10", "INFEASIBLE")]
)
def test_api_returns_200_and_decimal_strings_for_decision_statuses(
    risk_base: str, expected: str
) -> None:
    raw = raw_input(
        balance=risk_base, equity=risk_base, risk_base_value=risk_base
    )
    result = RiskFeasibilityResultMapper().map(raw, calculate(raw))
    api, _ = app_client(result)
    with api:
        response = api.get("/api/v1/risk/feasibility?signal_id=signal-1")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == expected
        for section, field in (
            ("account", "risk_base_value"),
            ("market", "entry_price"),
            ("volume", "normalized_lot"),
            ("calculation", "risk_amount"),
        ):
            assert isinstance(body[section][field], str)
        assert response.headers["cache-control"] == "no-store"
