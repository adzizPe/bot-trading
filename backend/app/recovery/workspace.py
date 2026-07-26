from __future__ import annotations

from contextlib import AbstractContextManager
import os
from pathlib import Path
import shutil
from uuid import uuid4


class PlaintextWorkspace(AbstractContextManager[Path]):
    """Restricted managed workspace with best-effort plaintext cleanup.

    Deletion cannot guarantee secure erasure on SSD or copy-on-write filesystems;
    encrypted volumes and restrictive ACLs remain the primary controls.
    """

    def __init__(self, work_root: Path, operation_id: str | None = None) -> None:
        if not work_root.is_absolute():
            raise ValueError("work root must be absolute")
        self.work_root = work_root
        self.operation_id = operation_id or str(uuid4())
        self.path = self.work_root / self.operation_id

    def __enter__(self) -> Path:
        self.work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.mkdir(mode=0o700, exist_ok=False)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        return self.path

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
