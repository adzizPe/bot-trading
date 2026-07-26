from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.operations.config import OperationalPolicy
from app.operations.restore_hold import RestoreHoldRecord, RestoreHoldStore
from app.operations.service_management import ServiceControlAdapter


class OfflineChecks(Protocol):
    async def no_writers(self) -> bool: ...

    async def no_processes(self) -> bool: ...

    async def no_listeners(self) -> bool: ...

    async def no_runtime_lease(self) -> bool: ...


@dataclass(frozen=True)
class HandoffResult:
    ready: bool
    reason: str
    hold: RestoreHoldRecord


@dataclass
class RecoveryHandoff:
    services: ServiceControlAdapter
    checks: OfflineChecks
    hold_store: RestoreHoldStore
    policy: OperationalPolicy = OperationalPolicy()
    edge_service: str = "TradingBotNginx"
    backend_service: str = "TradingBotBackend"

    async def enter(
        self,
        *,
        change_id: str,
        restore_id: str,
        operator_identity: str,
        reviewer_identity: str,
    ) -> HandoffResult:
        hold = self.hold_store.enter(
            change_id=change_id,
            reason="offline-recovery-handoff",
            operator_identity=operator_identity,
            reviewer_identity=reviewer_identity,
            restore_id=restore_id,
        )
        try:
            await self.services.stop(
                self.edge_service, self.policy.edge_drain_timeout_seconds
            )
        except Exception:
            return HandoffResult(False, "edge-stop-failed", hold)
        try:
            await self.services.stop(
                self.backend_service, self.policy.backend_shutdown_timeout_seconds
            )
        except Exception:
            return HandoffResult(False, "backend-stop-failed", hold)
        proofs = (
            ("writers-online", self.checks.no_writers),
            ("process-online", self.checks.no_processes),
            ("listener-online", self.checks.no_listeners),
            ("lease-online", self.checks.no_runtime_lease),
        )
        for reason, proof in proofs:
            try:
                offline = bool(await proof())
            except Exception:
                offline = False
            if not offline:
                return HandoffResult(False, reason, hold)
        return HandoffResult(True, "offline-handoff-ready", hold)

    def observe_restore_result(self, result: str) -> RestoreHoldRecord:
        """Records no authority: every restore outcome intentionally leaves HELD."""
        if result not in {"SUCCESS", "FAILED"}:
            raise ValueError("restore result must be SUCCESS or FAILED")
        hold = self.hold_store.read()
        if hold is None:
            return RestoreHoldRecord.fail_closed("restore-result-without-hold")
        return hold