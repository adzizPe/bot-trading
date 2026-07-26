from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.operations.config import OperationalPaths, ProcessManager, canonical_path

_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_PROHIBITED_ARG = re.compile(
    r"(?i)(password|passwd|secret|token|cookie|credential|private[ _-]?key|"
    r"authorization|bearer)"
)
_PROHIBITED_SERVICE = re.compile(
    r"(?i)(mt5|metatrader|connector|demo|paper|restore|recovery-drill|drill)"
)


class ServiceDefinitionError(ValueError):
    pass


class ServiceComponent(str, Enum):
    BACKEND = "BACKEND"
    EDGE = "EDGE"


class StartupMode(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"


class LifecycleOwner(str, Enum):
    NSSM = "NSSM"
    PM2 = "PM2"
    TASK_SCHEDULER = "TASK_SCHEDULER"
    STARTUP_FOLDER = "STARTUP_FOLDER"
    OPERATOR_SHELL = "OPERATOR_SHELL"


class RestartPolicy(BaseModel):
    delay_seconds: int = Field(default=30, ge=30)
    max_attempts: int = Field(default=3, ge=1, le=3)
    window_seconds: int = Field(default=600, ge=600, le=600)
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceDefinition(BaseModel):
    service_name: str
    component: ServiceComponent
    process_manager: ProcessManager
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    environment_source: Path
    startup_mode: StartupMode
    identity: str
    dependencies: tuple[str, ...] = ()
    shutdown_timeout_seconds: int = Field(ge=1, le=120)
    restart_policy: RestartPolicy = Field(default_factory=RestartPolicy)
    stdout_log: Path
    stderr_log: Path
    static_root: Path | None = None
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("service_name", "identity")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("service name/identity is not bounded")
        return value

    @field_validator(
        "executable", "working_directory", "environment_source", "stdout_log",
        "stderr_log", "static_root", mode="after",
    )
    @classmethod
    def validate_path(cls, value: Path | None) -> Path | None:
        return None if value is None else canonical_path(value)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 32:
            raise ValueError("service arguments must be bounded")
        if any(
            not argument or len(argument) > 256 or "=" in argument
            or _PROHIBITED_ARG.search(argument)
            for argument in value
        ):
            raise ValueError("service argv contains prohibited material")
        return value

    @field_validator("dependencies")
    @classmethod
    def canonical_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _SAFE_NAME.fullmatch(item) for item in value):
            raise ValueError("service dependency is invalid")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_component_fields(self) -> "ServiceDefinition":
        if _PROHIBITED_SERVICE.search(self.service_name):
            raise ValueError("prohibited trading/recovery autostart service")
        if self.component is ServiceComponent.BACKEND and self.static_root is not None:
            raise ValueError("backend cannot own a public static root")
        if self.component is ServiceComponent.EDGE and self.static_root is None:
            raise ValueError("edge must identify the Vite dist root")
        return self


@dataclass(frozen=True)
class ProcessObservation:
    process_id: int
    service_name: str
    executable: Path
    owner: LifecycleOwner


@dataclass(frozen=True)
class ListenerObservation:
    process_id: int
    host: str
    port: int


class ServiceControlAdapter(Protocol):
    async def service_state(self, service_name: str) -> str: ...

    async def start(self, service_name: str) -> None: ...

    async def stop(self, service_name: str, timeout_seconds: int) -> None: ...


class DiscoveryAdapter(Protocol):
    async def processes(self) -> tuple[ProcessObservation, ...]: ...

    async def listeners(self) -> tuple[ListenerObservation, ...]: ...

@dataclass
class FakeServiceAdapter:
    states: dict[str, str] = field(default_factory=dict)
    process_observations: tuple[ProcessObservation, ...] = ()
    listener_observations: tuple[ListenerObservation, ...] = ()
    actions: list[tuple[str, str]] = field(default_factory=list)

    async def service_state(self, service_name: str) -> str:
        return self.states.get(service_name, "STOPPED")

    async def start(self, service_name: str) -> None:
        self.actions.append(("START", service_name))
        self.states[service_name] = "RUNNING"

    async def stop(self, service_name: str, timeout_seconds: int) -> None:
        self.actions.append((f"STOP:{timeout_seconds}", service_name))
        self.states[service_name] = "STOPPED"

    async def processes(self) -> tuple[ProcessObservation, ...]:
        return self.process_observations

    async def listeners(self) -> tuple[ListenerObservation, ...]:
        return self.listener_observations


@dataclass(frozen=True)
class OwnershipClaim:
    resource_id: str
    owner: LifecycleOwner


def validate_single_ownership(claims: tuple[OwnershipClaim, ...]) -> None:
    owners: dict[str, set[LifecycleOwner]] = {}
    for claim in claims:
        owners.setdefault(claim.resource_id, set()).add(claim.owner)
    conflicts = sorted(resource for resource, values in owners.items() if len(values) != 1)
    if conflicts:
        raise ServiceDefinitionError(
            f"resources have multiple lifecycle owners: {','.join(conflicts)}"
        )


def canonical_nssm_definitions(
    paths: OperationalPaths,
    *,
    release_directory: Path,
    backend_identity: str = "svc-trading-backend",
    edge_identity: str = "svc-trading-edge",
) -> tuple[ServiceDefinition, ServiceDefinition]:
    release = canonical_path(release_directory)
    backend_root = release / "backend"
    frontend_dist = release / "frontend" / "dist"
    backend_name = "TradingBotBackend"
    edge_name = "TradingBotNginx"
    protected = paths.state_root / "protected"
    backend = ServiceDefinition(
        service_name=backend_name, component=ServiceComponent.BACKEND,
        process_manager=ProcessManager.NSSM,
        executable=backend_root / ".venv" / "Scripts" / "python.exe",
        arguments=(
            "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", "8000", "--workers", "1",
        ),
        working_directory=backend_root,
        environment_source=protected / "backend.environment",
        startup_mode=StartupMode.AUTOMATIC, identity=backend_identity,
        shutdown_timeout_seconds=120,
        stdout_log=paths.log_root / "backend.stdout.log",
        stderr_log=paths.log_root / "backend.stderr.log",
    )

    edge = ServiceDefinition(
        service_name=edge_name, component=ServiceComponent.EDGE,
        process_manager=ProcessManager.NSSM,
        executable=paths.nginx_root / "nginx.exe",
        arguments=("-p", str(paths.nginx_root), "-c", "conf/nginx.conf"),
        working_directory=paths.nginx_root,
        environment_source=protected / "edge.environment",
        startup_mode=StartupMode.MANUAL, identity=edge_identity,
        dependencies=(backend_name,), shutdown_timeout_seconds=30,
        stdout_log=paths.log_root / "nginx.stdout.log",
        stderr_log=paths.log_root / "nginx.stderr.log",
        static_root=frontend_dist,
    )
    validate_nssm_definitions((backend, edge))
    return backend, edge


def _argument_value(arguments: tuple[str, ...], flag: str) -> str | None:
    indexes = [index for index, value in enumerate(arguments) if value == flag]
    if len(indexes) != 1 or indexes[0] + 1 >= len(arguments):
        return None
    return arguments[indexes[0] + 1]


def validate_nssm_definitions(
    definitions: tuple[ServiceDefinition, ...],
) -> None:
    if len(definitions) != 2:
        raise ServiceDefinitionError("canonical NSSM topology requires two services")
    if any(item.process_manager is not ProcessManager.NSSM for item in definitions):
        raise ServiceDefinitionError("canonical definitions must be NSSM-owned")
    by_component = {item.component: item for item in definitions}
    if set(by_component) != {ServiceComponent.BACKEND, ServiceComponent.EDGE}:
        raise ServiceDefinitionError("topology requires one backend and one edge")
    backend = by_component[ServiceComponent.BACKEND]
    edge = by_component[ServiceComponent.EDGE]
    if backend.executable.name.casefold() != "python.exe" or (
        ".venv" not in {part.casefold() for part in backend.executable.parts}
    ):
        raise ServiceDefinitionError("backend executable must be venv Python")
    required = {
        "--host": "127.0.0.1", "--port": "8000", "--workers": "1",
    }
    if backend.arguments[:3] != ("-m", "uvicorn", "app.main:app") or any(
        _argument_value(backend.arguments, flag) != expected
        for flag, expected in required.items()
    ):
        raise ServiceDefinitionError("backend Uvicorn arguments are not canonical")
    if any(value in backend.arguments for value in ("--reload", "--factory")):
        raise ServiceDefinitionError("backend reload/factory mode is prohibited")
    if edge.executable.name.casefold() != "nginx.exe":
        raise ServiceDefinitionError("edge executable must be native Nginx")
    if edge.dependencies != (backend.service_name,):
        raise ServiceDefinitionError("edge must depend on backend")
    if edge.static_root is None or edge.static_root.parts[-2:] != ("frontend", "dist"):
        raise ServiceDefinitionError("edge must serve the release Vite dist")


class PM2Options(BaseModel):
    service_name: str
    exec_mode: str = "fork"
    instances: int = Field(default=1, ge=1, le=1)
    watch: bool = False
    reload: bool = False
    autorestart: bool = True
    interpreter: Path | None = None
    restart_delay_seconds: int = Field(default=30, ge=30)
    max_restarts: int = Field(default=3, ge=1, le=3)
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("PM2 service name is invalid")
        return value

    @field_validator("interpreter", mode="after")
    @classmethod
    def validate_interpreter(cls, value: Path | None) -> Path | None:
        return None if value is None else canonical_path(value)

    @model_validator(mode="after")
    def validate_fork_contract(self) -> "PM2Options":
        if self.exec_mode != "fork" or self.instances != 1:
            raise ValueError("PM2 must use one fork-mode instance")
        if self.watch or self.reload or not self.autorestart:
            raise ValueError("PM2 watch/reload is prohibited and restart must be bounded")
        return self


def validate_pm2_alternative(
    definitions: tuple[ServiceDefinition, ...],
    options: tuple[PM2Options, ...],
    *,
    selected_process_manager: ProcessManager,
    ownership_claims: tuple[OwnershipClaim, ...],
) -> None:
    if selected_process_manager is not ProcessManager.PM2:
        raise ServiceDefinitionError("PM2 requires an explicit host selection")
    if any(item.process_manager is not ProcessManager.PM2 for item in definitions):
        raise ServiceDefinitionError("PM2 and NSSM definitions cannot be mixed")
    if len(definitions) != 2 or {item.component for item in definitions} != {
        ServiceComponent.BACKEND, ServiceComponent.EDGE,
    }:
        raise ServiceDefinitionError("PM2 topology requires one backend and one edge")
    option_map = {item.service_name: item for item in options}
    if set(option_map) != {item.service_name for item in definitions}:
        raise ServiceDefinitionError("every PM2 process requires one options contract")
    backend = next(
        item for item in definitions if item.component is ServiceComponent.BACKEND
    )
    backend_options = option_map[backend.service_name]
    if backend_options.interpreter != backend.executable or (
        backend_options.interpreter is None
        or ".venv" not in {
            part.casefold() for part in backend_options.interpreter.parts
        }
    ):
        raise ServiceDefinitionError("PM2 backend requires explicit venv interpreter")
    if _argument_value(backend.arguments, "--host") != "127.0.0.1" or (
        _argument_value(backend.arguments, "--workers") != "1"
    ):
        raise ServiceDefinitionError("PM2 backend must remain private and single-worker")
    validate_single_ownership(ownership_claims)
    claims = {claim.resource_id: claim.owner for claim in ownership_claims}
    if any(claims.get(item.service_name) is not LifecycleOwner.PM2 for item in definitions):
        raise ServiceDefinitionError("PM2 services require exclusive PM2 ownership")


def validate_private_backend(
    definitions: tuple[ServiceDefinition, ...],
    processes: tuple[ProcessObservation, ...],
    listeners: tuple[ListenerObservation, ...],
) -> None:
    backend_definitions = [
        item for item in definitions if item.component is ServiceComponent.BACKEND
    ]
    if len(backend_definitions) != 1:
        raise ServiceDefinitionError("exactly one backend definition is required")
    backend = backend_definitions[0]
    backend_processes = [
        item for item in processes if item.service_name == backend.service_name
    ]
    if len(backend_processes) != 1:
        raise ServiceDefinitionError("exactly one backend process is required")
    process = backend_processes[0]
    expected_owner = (
        LifecycleOwner.NSSM
        if backend.process_manager is ProcessManager.NSSM
        else LifecycleOwner.PM2
    )
    if process.owner is not expected_owner:
        raise ServiceDefinitionError("backend process lifecycle owner is inconsistent")
    backend_listeners = [
        item for item in listeners if item.process_id == process.process_id
    ]
    if not backend_listeners or any(
        item.host not in {"127.0.0.1", "::1"} or item.port != 8000
        for item in backend_listeners
    ):
        raise ServiceDefinitionError("backend listener must be loopback-only on port 8000")


def pm2_equivalent_definitions(
    definitions: tuple[ServiceDefinition, ...],
) -> tuple[ServiceDefinition, ...]:
    """Copy canonical definitions for explicit PM2 review; never installs anything."""
    return tuple(
        item.model_copy(update={"process_manager": ProcessManager.PM2})
        for item in definitions
    )
