from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import os
from pathlib import Path
import struct
from uuid import uuid4

import pytest

from app.recovery.artifact import (
    ArtifactAuthenticationError,
    ArtifactCodec,
    ArtifactError,
    ArtifactFormatError,
    sha256_file,
)
from app.recovery.keys import (
    EncryptionKeyError,
    decode_encryption_key,
    load_encryption_key,
)
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
)
from app.recovery.workspace import PlaintextWorkspace

UTC_NOW = datetime(2026, 2, 3, 4, 5, tzinfo=timezone.utc)
PREFIX_SIZE = struct.calcsize(">8sHI")


def _key(seed: int = 1) -> bytes:
    return bytes([seed]) * 32


def _encrypt(
    tmp_path: Path,
    plaintext: bytes = b"sqlite-snapshot" * 100,
    *,
    compression: Compression = Compression.GZIP,
) -> tuple[ArtifactCodec, Path, object]:
    source = tmp_path / "snapshot.db"
    artifact = tmp_path / "artifact.btbak"
    source.write_bytes(plaintext)
    codec = ArtifactCodec(chunk_bytes=97)
    result = codec.encrypt(
        source,
        artifact,
        backup_id=uuid4(),
        application_version="test",
        database_revision="head",
        created_at=UTC_NOW,
        compression=compression,
        key=_key(),
    )
    return codec, artifact, result


@pytest.mark.parametrize("compression", [Compression.GZIP, Compression.NONE])
def test_streaming_artifact_round_trip_and_whole_container_checksum(
    tmp_path: Path, compression: Compression
) -> None:
    plaintext = os.urandom(512 * 1024)
    codec, artifact, result = _encrypt(tmp_path, plaintext, compression=compression)
    restored = tmp_path / "restored.db"

    header = codec.decrypt(artifact, restored, key=_key())

    assert restored.read_bytes() == plaintext
    assert header.compression is compression
    assert result.size == artifact.stat().st_size
    assert result.checksum_sha256 == sha256_file(artifact)
    assert not artifact.with_name("artifact.btbak.partial").exists()


def test_same_plaintext_uses_random_nonce_and_distinct_container(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot.db"
    source.write_bytes(b"same plaintext" * 1000)
    codec = ArtifactCodec()
    artifacts = [tmp_path / f"artifact-{index}.btbak" for index in range(2)]
    results = [
        codec.encrypt(
            source,
            artifact,
            backup_id=uuid4(),
            application_version="test",
            database_revision="head",
            created_at=UTC_NOW,
            compression=Compression.GZIP,
            key=_key(),
        )
        for artifact in artifacts
    ]

    assert results[0].header.nonce != results[1].header.nonce
    assert artifacts[0].read_bytes() != artifacts[1].read_bytes()


def test_wrong_key_and_tampering_preserve_existing_target(tmp_path: Path) -> None:
    codec, artifact, _ = _encrypt(tmp_path)
    target = tmp_path / "active.db"
    target.write_bytes(b"must remain unchanged")

    with pytest.raises(ArtifactAuthenticationError):
        codec.decrypt(artifact, target, key=_key(2))
    assert target.read_bytes() == b"must remain unchanged"

    payload = bytearray(artifact.read_bytes())
    payload[-17] ^= 1
    artifact.write_bytes(payload)
    with pytest.raises(ArtifactAuthenticationError):
        codec.decrypt(artifact, target, key=_key())
    assert target.read_bytes() == b"must remain unchanged"


@pytest.mark.parametrize("mutation", ["header", "ciphertext", "tag"])
def test_authenticated_regions_reject_one_byte_mutation(
    tmp_path: Path, mutation: str
) -> None:
    codec, artifact, _ = _encrypt(tmp_path)
    payload = bytearray(artifact.read_bytes())
    _, _, header_size = struct.unpack(">8sHI", payload[:PREFIX_SIZE])
    if mutation == "header":
        index = PREFIX_SIZE + header_size - 2
    elif mutation == "ciphertext":
        index = PREFIX_SIZE + header_size
    else:
        index = len(payload) - 1
    payload[index] ^= 1
    artifact.write_bytes(payload)

    with pytest.raises(ArtifactError):
        codec.decrypt(artifact, tmp_path / "decoded.db", key=_key())
    assert not (tmp_path / "decoded.db").exists()


def test_truncation_and_unsupported_version_fail_closed(tmp_path: Path) -> None:
    codec, artifact, _ = _encrypt(tmp_path)
    original = artifact.read_bytes()
    artifact.write_bytes(original[:-8])
    with pytest.raises(ArtifactError):
        codec.decrypt(artifact, tmp_path / "truncated.db", key=_key())

    mutated = bytearray(original)
    mutated[8:10] = (99).to_bytes(2, "big")
    artifact.write_bytes(mutated)
    with pytest.raises(ArtifactFormatError, match="version"):
        codec.decrypt(artifact, tmp_path / "version.db", key=_key())


def test_header_manifest_mismatch_is_rejected_before_publication(
    tmp_path: Path,
) -> None:
    codec, artifact, result = _encrypt(tmp_path)
    manifest = BackupManifest(
        backup_id=uuid4(),
        database_revision=result.header.database_revision,
        created_at=result.header.created_at,
        source_database="active.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=result.header.compression,
        status=BackupLifecycleStatus.IN_PROGRESS,
    )
    with pytest.raises(ArtifactFormatError, match="manifest"):
        codec.decrypt(
            artifact,
            tmp_path / "mismatch.db",
            key=_key(),
            expected_manifest=manifest,
        )


def test_encrypt_failure_cleans_partial_and_never_publishes_final(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot.db"
    artifact = tmp_path / "artifact.btbak"
    source.write_bytes(os.urandom(256 * 1024))

    def fail(point: str) -> None:
        if point == "artifact_encrypt":
            raise OSError("injected disk full")

    with pytest.raises(OSError, match="disk full"):
        ArtifactCodec(chunk_bytes=1024).encrypt(
            source,
            artifact,
            backup_id=uuid4(),
            application_version="test",
            database_revision="head",
            created_at=UTC_NOW,
            compression=Compression.GZIP,
            key=_key(),
            fault=fail,
        )
    assert not artifact.exists()
    assert not artifact.with_name("artifact.btbak.partial").exists()


def test_key_input_is_exact_environment_or_secure_prompt_only() -> None:
    encoded = b64encode(_key()).decode("ascii")
    assert decode_encryption_key(encoded) == _key()
    assert (
        load_encryption_key(environ={"SAFE_KEY": encoded}, env_name="SAFE_KEY")
        == _key()
    )
    assert (
        load_encryption_key(environ={}, interactive=True, prompt=lambda _: encoded)
        == _key()
    )
    with pytest.raises(EncryptionKeyError, match="unavailable"):
        load_encryption_key(environ={})
    with pytest.raises(EncryptionKeyError, match="exactly 32"):
        decode_encryption_key(b64encode(b"short").decode("ascii"))


def test_plaintext_workspace_is_restricted_and_cleaned_on_all_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".work"
    with PlaintextWorkspace(root, "success") as workspace:
        (workspace / "snapshot.db").write_bytes(b"plaintext")
        assert workspace.is_dir()
    assert not (root / "success").exists()

    with pytest.raises(RuntimeError, match="stop"):
        with PlaintextWorkspace(root, "failure") as workspace:
            (workspace / "snapshot.db").write_bytes(b"plaintext")
            raise RuntimeError("stop")
    assert not (root / "failure").exists()


def test_artifact_pipeline_memory_is_bounded_by_chunks(tmp_path: Path) -> None:
    import tracemalloc

    source = tmp_path / "large.db"
    with source.open("wb") as stream:
        for _ in range(2048):
            stream.write(b"bounded-memory-page" * 256)
    artifact = tmp_path / "large.btbak"
    tracemalloc.start()
    try:
        ArtifactCodec(chunk_bytes=16 * 1024).encrypt(
            source,
            artifact,
            backup_id=uuid4(),
            application_version="test",
            database_revision="head",
            created_at=UTC_NOW,
            compression=Compression.GZIP,
            key=_key(),
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert source.stat().st_size > 8 * 1024 * 1024
    assert peak < 4 * 1024 * 1024


def test_key_material_is_absent_from_container_and_error_output(
    tmp_path: Path,
) -> None:
    encoded = b64encode(_key()).decode("ascii")
    codec, artifact, _ = _encrypt(tmp_path)
    assert _key() not in artifact.read_bytes()
    assert encoded.encode("ascii") not in artifact.read_bytes()
    with pytest.raises(ArtifactAuthenticationError) as failure:
        codec.decrypt(artifact, tmp_path / "wrong.db", key=_key(2))
    assert encoded not in str(failure.value)
