from __future__ import annotations

import os
from pathlib import Path

from app.operations.config import canonical_path
from app.operations.models import OperatorEvidencePackage, contains_sensitive_content


class EvidenceQuarantinedError(ValueError):
    """Raised before publication when evidence contains prohibited material."""


class EvidenceAlreadyPublishedError(FileExistsError):
    """Raised when immutable signed-off evidence already exists."""


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = canonical_path(root)

    def publish(
        self,
        package: OperatorEvidencePackage,
        *,
        secret_canaries: tuple[str, ...] = (),
    ) -> Path:
        payload = package.canonical_bytes()
        if contains_sensitive_content(
            payload.decode("ascii"), canaries=secret_canaries
        ):
            raise EvidenceQuarantinedError(
                "evidence quarantined by redaction scanner"
            )
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.root / f"{package.event_id}.json"
        if target.exists():
            raise EvidenceAlreadyPublishedError(
                "signed-off evidence is immutable"
            )
        partial = target.with_name(f"{target.name}.partial")
        descriptor = os.open(
            partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise EvidenceAlreadyPublishedError(
                    "signed-off evidence is immutable"
                )
            os.replace(partial, target)
        except BaseException:
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
            raise
        return target
