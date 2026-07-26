from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util import CommandError
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


class CompatibilityKind(str, Enum):
    EXACT = "EXACT"
    ANCESTOR = "ANCESTOR"


class RevisionRejection(str, Enum):
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    NEWER = "NEWER"
    DIVERGENT = "DIVERGENT"
    REPOSITORY_INVALID = "REPOSITORY_INVALID"
    MIGRATION_FAILED = "MIGRATION_FAILED"


class AlembicCompatibilityError(Exception):
    def __init__(self, rejection: RevisionRejection) -> None:
        super().__init__(rejection.value)
        self.rejection = rejection


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    source_revision: str
    target_revision: str
    kind: CompatibilityKind
    migrated: bool = False


class AlembicCompatibilityService:
    """Resolve and migrate one allowed Alembic lineage without app startup."""

    def __init__(
        self,
        script_location: Path,
        *,
        target_revision: str | None = None,
    ) -> None:
        location = script_location.resolve()
        if not location.is_dir():
            raise ValueError("migration script location must be a directory")
        self.script_location = location
        self._scripts = ScriptDirectory(str(location))
        self._target_revision = target_revision

    @property
    def repository_head(self) -> str:
        if self._target_revision is not None:
            self._known_revision(self._target_revision)
            return self._target_revision
        heads = self._scripts.get_heads()
        if len(heads) != 1:
            raise AlembicCompatibilityError(RevisionRejection.REPOSITORY_INVALID)
        return heads[0]

    def read_database_revision(self, database: Path) -> str:
        if not database.is_file():
            raise AlembicCompatibilityError(RevisionRejection.MISSING)
        uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                rows = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 2"
                ).fetchall()
        except sqlite3.DatabaseError as error:
            raise AlembicCompatibilityError(RevisionRejection.MISSING) from error
        if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0].strip():
            raise AlembicCompatibilityError(RevisionRejection.MISSING)
        return rows[0][0]

    def classify(self, revision: str | None) -> CompatibilityDecision:
        if revision is None or not revision.strip():
            raise AlembicCompatibilityError(RevisionRejection.MISSING)
        source = revision.strip()
        target = self.repository_head
        self._known_revision(target)
        if source == target:
            return CompatibilityDecision(source, target, CompatibilityKind.EXACT)
        try:
            self._known_revision(source)
        except AlembicCompatibilityError as error:
            if error.rejection is RevisionRejection.UNKNOWN:
                raise
            raise
        target_lineage = self._lineage(target)
        if source in target_lineage:
            return CompatibilityDecision(source, target, CompatibilityKind.ANCESTOR)
        source_lineage = self._lineage(source)
        rejection = (
            RevisionRejection.NEWER
            if target in source_lineage
            else RevisionRejection.DIVERGENT
        )
        raise AlembicCompatibilityError(rejection)

    def inspect_candidate(self, database: Path) -> CompatibilityDecision:
        return self.classify(self.read_database_revision(database))

    def migrate_candidate(
        self, database: Path, decision: CompatibilityDecision
    ) -> CompatibilityDecision:
        if decision.target_revision != self.repository_head:
            raise AlembicCompatibilityError(RevisionRejection.REPOSITORY_INVALID)
        if decision.kind is CompatibilityKind.EXACT:
            return decision
        if decision.kind is not CompatibilityKind.ANCESTOR:
            raise AlembicCompatibilityError(RevisionRejection.DIVERGENT)
        candidate = database.resolve()
        if not candidate.is_file():
            raise AlembicCompatibilityError(RevisionRejection.MISSING)
        url = URL.create("sqlite", database=str(candidate))
        engine = create_engine(url, future=True)
        try:
            revisions = list(
                reversed(
                    list(
                        self._scripts.iterate_revisions(
                            decision.target_revision,
                            decision.source_revision,
                        )
                    )
                )
            )
            with engine.begin() as connection:
                current = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version LIMIT 2"
                ).fetchall()
                if current != [(decision.source_revision,)]:
                    raise AlembicCompatibilityError(RevisionRejection.MISSING)
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    for revision in revisions:
                        revision.module.upgrade()
                        connection.exec_driver_sql(
                            "UPDATE alembic_version SET version_num = ?",
                            (revision.revision,),
                        )
        except AlembicCompatibilityError:
            raise
        except Exception as error:
            raise AlembicCompatibilityError(
                RevisionRejection.MIGRATION_FAILED
            ) from error
        finally:
            engine.dispose()
        migrated = self.read_database_revision(candidate)
        if migrated != decision.target_revision:
            raise AlembicCompatibilityError(RevisionRejection.MIGRATION_FAILED)
        return CompatibilityDecision(
            decision.source_revision,
            decision.target_revision,
            CompatibilityKind.ANCESTOR,
            migrated=True,
        )

    def _known_revision(self, revision: str) -> None:
        try:
            resolved = self._scripts.get_revision(revision)
        except (ResolutionError, CommandError) as error:
            raise AlembicCompatibilityError(RevisionRejection.UNKNOWN) from error
        if resolved is None:
            raise AlembicCompatibilityError(RevisionRejection.UNKNOWN)

    def _lineage(self, revision: str) -> frozenset[str]:
        try:
            return frozenset(
                item.revision
                for item in self._scripts.iterate_revisions(revision, "base")
            )
        except ResolutionError as error:
            raise AlembicCompatibilityError(RevisionRejection.UNKNOWN) from error
