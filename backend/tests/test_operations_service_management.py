from pathlib import Path

from hypothesis import given, strategies as st
from pydantic import ValidationError
import pytest

from app.operations import (
    FakeServiceAdapter,
    LifecycleOwner,
    ListenerObservation,
    OperationalPaths,
    OwnershipClaim,
    PM2Options,
    ProcessManager,
    ProcessObservation,
    ServiceComponent,
    ServiceDefinitionError,
    canonical_nssm_definitions,
    pm2_equivalent_definitions,
    validate_pm2_alternative,
    validate_private_backend,
    validate_single_ownership,
)
from app.recovery.leases import DatabaseRuntimeLease, LeaseUnavailableError


def paths(root: Path) -> OperationalPaths:
    return OperationalPaths(
        release_root=root / "releases", state_root=root / "state",
        evidence_root=root / "evidence", log_root=root / "logs",
        certificate_root=root / "certificates", nginx_root=root / "nginx",
        recovery_root=root / "recovery", active_reference=root / "current",
        active_sqlite=root / "data" / "app.db",
    )


def definitions(root: Path):
    operational_paths = paths(root)
    return canonical_nssm_definitions(
        operational_paths,
        release_directory=operational_paths.release_root / "release-1",
    )


def test_canonical_nssm_templates_are_native_private_and_single_worker(
    tmp_path: Path,
) -> None:
    backend, edge = definitions(tmp_path)
    assert backend.process_manager is ProcessManager.NSSM
    assert backend.executable.parts[-4:] == (
        "backend", ".venv", "Scripts", "python.exe"
    )
    assert backend.arguments == (
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
        "--port", "8000", "--workers", "1",
    )
    assert backend.shutdown_timeout_seconds == 120
    assert backend.restart_policy.delay_seconds == 30
    assert backend.restart_policy.max_attempts == 3
    assert edge.dependencies == (backend.service_name,)
    assert edge.startup_mode.value == "MANUAL"
    assert edge.executable.name == "nginx.exe"
    assert edge.static_root is not None
    assert edge.static_root.parts[-2:] == ("frontend", "dist")
    assert edge.shutdown_timeout_seconds == 30

def test_service_definition_rejects_secret_argv_and_prohibited_autostart(
    tmp_path: Path,
) -> None:
    backend, _ = definitions(tmp_path)
    with pytest.raises(ValidationError, match="prohibited material"):
        type(backend).model_validate(
            {
                **backend.model_dump(),
                "arguments": (*backend.arguments, "--token", "synthetic-canary"),
            }
        )
    values = backend.model_dump()
    values["service_name"] = "MetaTrader5Terminal"
    with pytest.raises(ValidationError, match="prohibited"):
        type(backend).model_validate(values)


@pytest.mark.asyncio
async def test_fake_adapter_has_bounded_actions_and_no_install_authority() -> None:
    adapter = FakeServiceAdapter()
    await adapter.start("TradingBotBackend")
    await adapter.stop("TradingBotBackend", 120)
    assert adapter.actions == [
        ("START", "TradingBotBackend"),
        ("STOP:120", "TradingBotBackend"),
    ]
    assert not hasattr(adapter, "install")


def test_dual_or_mixed_lifecycle_ownership_is_rejected() -> None:
    with pytest.raises(ServiceDefinitionError, match="multiple lifecycle owners"):
        validate_single_ownership((
            OwnershipClaim("TradingBotBackend", LifecycleOwner.NSSM),
            OwnershipClaim("TradingBotBackend", LifecycleOwner.PM2),
        ))
    with pytest.raises(ServiceDefinitionError, match="multiple lifecycle owners"):
        validate_single_ownership((
            OwnershipClaim("TradingBotBackend", LifecycleOwner.PM2),
            OwnershipClaim("TradingBotBackend", LifecycleOwner.TASK_SCHEDULER),
        ))


def test_pm2_alternative_requires_explicit_exclusive_equivalent_contract(
    tmp_path: Path,
) -> None:
    nssm = definitions(tmp_path)
    pm2 = pm2_equivalent_definitions(nssm)
    backend = next(item for item in pm2 if item.component is ServiceComponent.BACKEND)
    options = tuple(
        PM2Options(
            service_name=item.service_name,
            interpreter=backend.executable if item is backend else None,
        )
        for item in pm2
    )
    claims = tuple(
        OwnershipClaim(item.service_name, LifecycleOwner.PM2) for item in pm2
    )
    validate_pm2_alternative(
        pm2, options, selected_process_manager=ProcessManager.PM2,
        ownership_claims=claims,
    )
    with pytest.raises(ServiceDefinitionError, match="explicit host selection"):
        validate_pm2_alternative(
            pm2, options, selected_process_manager=ProcessManager.NSSM,
            ownership_claims=claims,
        )

def test_pm2_rejects_watch_reload_cluster_and_mixed_manager(tmp_path: Path) -> None:
    nssm = definitions(tmp_path)
    backend, edge = pm2_equivalent_definitions(nssm)
    for invalid in (
        {"exec_mode": "cluster"}, {"instances": 2}, {"watch": True},
        {"reload": True},
    ):
        with pytest.raises(ValidationError):
            PM2Options(
                service_name=backend.service_name,
                interpreter=backend.executable,
                **invalid,
            )
    options = (
        PM2Options(
            service_name=backend.service_name, interpreter=backend.executable
        ),
        PM2Options(service_name=edge.service_name),
    )
    mixed = (backend, edge.model_copy(update={"process_manager": ProcessManager.NSSM}))
    with pytest.raises(ServiceDefinitionError, match="cannot be mixed"):
        validate_pm2_alternative(
            mixed, options, selected_process_manager=ProcessManager.PM2,
            ownership_claims=(
                OwnershipClaim(backend.service_name, LifecycleOwner.PM2),
                OwnershipClaim(edge.service_name, LifecycleOwner.PM2),
            ),
        )


def test_backend_exposure_is_loopback_only_even_when_edge_is_absent(
    tmp_path: Path,
) -> None:
    services = definitions(tmp_path)
    backend = services[0]
    process = ProcessObservation(
        process_id=101, service_name=backend.service_name,
        executable=backend.executable, owner=LifecycleOwner.NSSM,
    )
    validate_private_backend(
        services, (process,),
        (ListenerObservation(process_id=101, host="127.0.0.1", port=8000),),
    )
    with pytest.raises(ServiceDefinitionError, match="loopback-only"):
        validate_private_backend(
            services, (process,),
            (ListenerObservation(process_id=101, host="0.0.0.0", port=8000),),
        )


@given(st.sampled_from(["0.0.0.0", "192.0.2.10", "::", "203.0.113.7"]))
def test_property_public_hosts_can_never_satisfy_backend_exposure(
    host: str,
) -> None:
    assert host not in {"127.0.0.1", "::1"}


def test_second_file_backed_backend_is_rejected_by_runtime_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "synthetic.db"
    first = DatabaseRuntimeLease(database, operation_id="backend-one")
    second = DatabaseRuntimeLease(database, operation_id="backend-two")
    first.acquire()
    try:
        with pytest.raises(LeaseUnavailableError):
            second.acquire()
    finally:
        first.release()
        second.release()
