from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import struct
from typing import Any, BinaryIO, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.recovery.types import (
    BACKUP_FORMAT_VERSION,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
)

_MAGIC = b"BTBAK\r\n\x1a"
_PREFIX = struct.Struct(">8sHI")
_TAG_BYTES = 16
_NONCE_BYTES = 12
_MAX_HEADER_BYTES = 64 * 1024
_CHUNK_BYTES = 128 * 1024


class ArtifactError(Exception):
    """Base class for stable artifact failures."""


class ArtifactFormatError(ArtifactError):
    """The container structure or authenticated metadata is invalid."""


class ArtifactAuthenticationError(ArtifactError):
    """The key or authenticated container bytes are invalid."""


class ArtifactFault(Protocol):
    def __call__(self, point: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ArtifactHeader:
    backup_id: UUID
    application_version: str
    database_revision: str
    created_at: datetime
    compression: Compression
    encryption: EncryptionAlgorithm
    nonce: bytes
    plaintext_size: int

    def __post_init__(self) -> None:
        if not self.application_version or not self.database_revision:
            raise ValueError("artifact versions are required")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.created_at.utcoffset().total_seconds() != 0:
            raise ValueError("created_at must use UTC")
        if self.encryption is not EncryptionAlgorithm.AES_256_GCM:
            raise ValueError("artifact v1 requires AES-256-GCM")
        if len(self.nonce) != _NONCE_BYTES:
            raise ValueError("artifact nonce must be 96 bits")
        if self.plaintext_size < 0:
            raise ValueError("plaintext size must be non-negative")


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    header: ArtifactHeader
    size: int
    checksum_sha256: str


def sha256_file(path: Path, *, chunk_bytes: int = _CHUNK_BYTES) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_header_manifest(header: ArtifactHeader, manifest: BackupManifest) -> None:
    expected = (
        manifest.backup_id,
        manifest.application_version,
        manifest.database_revision,
        manifest.created_at,
        manifest.compression,
        manifest.encryption,
    )
    actual = (
        header.backup_id,
        header.application_version,
        header.database_revision,
        header.created_at,
        header.compression,
        header.encryption,
    )
    if actual != expected:
        raise ArtifactFormatError("artifact header does not match manifest")


class _EncryptingWriter(io.RawIOBase):
    def __init__(
        self,
        stream: BinaryIO,
        encryptor: Any,
        digest: Any,
        fault: ArtifactFault | None,
    ) -> None:
        self._stream = stream
        self._encryptor = encryptor
        self._digest = digest
        self._fault = fault

    def writable(self) -> bool:
        return True

    def write(self, value: bytes | bytearray) -> int:
        if self._fault is not None:
            self._fault("artifact_encrypt")
        plaintext = bytes(value)
        ciphertext = self._encryptor.update(plaintext)
        if ciphertext:
            self._stream.write(ciphertext)
            self._digest.update(ciphertext)
        return len(plaintext)


class ArtifactCodec:
    """Versioned streaming gzip + AES-256-GCM container codec."""

    def __init__(self, *, chunk_bytes: int = _CHUNK_BYTES) -> None:
        if chunk_bytes <= 0:
            raise ValueError("chunk size must be positive")
        self.chunk_bytes = chunk_bytes

    def encrypt(
        self,
        source: Path,
        destination: Path,
        *,
        backup_id: UUID,
        application_version: str,
        database_revision: str,
        created_at: datetime,
        compression: Compression,
        key: bytes,
        fault: ArtifactFault | None = None,
    ) -> ArtifactResult:
        _validate_key(key)
        nonce = os.urandom(_NONCE_BYTES)
        header = ArtifactHeader(
            backup_id=backup_id,
            application_version=application_version,
            database_revision=database_revision,
            created_at=created_at.astimezone(timezone.utc),
            compression=compression,
            encryption=EncryptionAlgorithm.AES_256_GCM,
            nonce=nonce,
            plaintext_size=source.stat().st_size,
        )
        header_bytes = _encode_header(header)
        prefix = _PREFIX.pack(_MAGIC, BACKUP_FORMAT_VERSION, len(header_bytes))
        aad = prefix + header_bytes
        partial = destination.with_name(f"{destination.name}.partial")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        digest = hashlib.sha256()
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(aad)

        try:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(aad)
                digest.update(aad)
                writer = _EncryptingWriter(stream, encryptor, digest, fault)
                with source.open("rb") as plaintext:
                    if compression is Compression.GZIP:
                        if fault is not None:
                            fault("artifact_compression")
                        with gzip.GzipFile(
                            fileobj=writer, mode="wb", compresslevel=6, mtime=0
                        ) as compressor:
                            for chunk in iter(
                                lambda: plaintext.read(self.chunk_bytes), b""
                            ):
                                compressor.write(chunk)
                    elif compression is Compression.NONE:
                        for chunk in iter(
                            lambda: plaintext.read(self.chunk_bytes), b""
                        ):
                            writer.write(chunk)
                    else:
                        raise ArtifactFormatError("unsupported compression")
                final_ciphertext = encryptor.finalize()
                if final_ciphertext:
                    stream.write(final_ciphertext)
                    digest.update(final_ciphertext)
                stream.write(encryptor.tag)
                digest.update(encryptor.tag)
                stream.flush()
                if fault is not None:
                    fault("artifact_fsync")
                os.fsync(stream.fileno())
            if fault is not None:
                fault("artifact_rename")
            os.replace(partial, destination)
            _fsync_directory(destination.parent)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return ArtifactResult(
            header=header,
            size=destination.stat().st_size,
            checksum_sha256=digest.hexdigest(),
        )

    def read_header(self, artifact: Path) -> ArtifactHeader:
        with artifact.open("rb") as stream:
            _, header = _read_prefix_and_header(stream)
        return header

    def decrypt(
        self,
        artifact: Path,
        destination: Path,
        *,
        key: bytes,
        expected_manifest: BackupManifest | None = None,
        fault: ArtifactFault | None = None,
    ) -> ArtifactHeader:
        _validate_key(key)
        partial = destination.with_name(f"{destination.name}.partial")
        authenticated = destination.with_name(f"{destination.name}.payload.partial")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        authenticated.unlink(missing_ok=True)
        try:
            with artifact.open("rb") as source:
                aad, header = _read_prefix_and_header(source)
                if expected_manifest is not None:
                    validate_header_manifest(header, expected_manifest)
                payload_start = source.tell()
                artifact_size = artifact.stat().st_size
                ciphertext_size = artifact_size - payload_start - _TAG_BYTES
                if ciphertext_size < 0:
                    raise ArtifactFormatError("artifact is truncated")
                source.seek(artifact_size - _TAG_BYTES)
                tag = _read_exact(source, _TAG_BYTES)
                source.seek(payload_start)
                decryptor = Cipher(
                    algorithms.AES(key), modes.GCM(header.nonce, tag)
                ).decryptor()
                decryptor.authenticate_additional_data(aad)
                reader = _DecryptingReader(
                    source, decryptor, ciphertext_size, self.chunk_bytes, fault
                )
                descriptor = os.open(
                    authenticated, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb", closefd=True) as payload:
                    for chunk in iter(lambda: reader.read(self.chunk_bytes), b""):
                        payload.write(chunk)
                    reader.finish()
                    payload.flush()
                    os.fsync(payload.fileno())
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with (
                authenticated.open("rb") as payload,
                os.fdopen(descriptor, "wb", closefd=True) as output,
            ):
                self._decode_payload(payload, output, header.compression)
                output.flush()
                os.fsync(output.fileno())
            if partial.stat().st_size != header.plaintext_size:
                raise ArtifactFormatError("plaintext size does not match header")
            os.replace(partial, destination)
            return header
        except InvalidTag as error:
            raise ArtifactAuthenticationError(
                "artifact authentication failed"
            ) from error
        except (OSError, EOFError, gzip.BadGzipFile, struct.error) as error:
            raise ArtifactFormatError("artifact decode failed") from error
        finally:
            partial.unlink(missing_ok=True)
            authenticated.unlink(missing_ok=True)

    def _decode_payload(
        self,
        reader: BinaryIO,
        output: BinaryIO,
        compression: Compression,
    ) -> None:
        if compression is Compression.GZIP:
            with gzip.GzipFile(fileobj=reader, mode="rb") as decompressor:
                for chunk in iter(lambda: decompressor.read(self.chunk_bytes), b""):
                    output.write(chunk)
        elif compression is Compression.NONE:
            for chunk in iter(lambda: reader.read(self.chunk_bytes), b""):
                output.write(chunk)
        else:
            raise ArtifactFormatError("unsupported compression")


class _DecryptingReader(io.RawIOBase):
    def __init__(
        self,
        stream: BinaryIO,
        decryptor: Any,
        remaining: int,
        chunk_bytes: int,
        fault: ArtifactFault | None,
    ) -> None:
        self._stream = stream
        self._decryptor = decryptor
        self._remaining = remaining
        self._chunk_bytes = chunk_bytes
        self._fault = fault
        self._buffer = bytearray()
        self._finalized = False

    def readable(self) -> bool:
        return True

    def readinto(self, target: bytearray) -> int:
        requested = len(target)
        while len(self._buffer) < requested and not self._finalized:
            self._fill()
        count = min(requested, len(self._buffer))
        target[:count] = self._buffer[:count]
        del self._buffer[:count]
        return count

    def _fill(self) -> None:
        if self._remaining:
            if self._fault is not None:
                self._fault("artifact_decrypt")
            chunk = _read_exact(self._stream, min(self._remaining, self._chunk_bytes))
            self._remaining -= len(chunk)
            self._buffer.extend(self._decryptor.update(chunk))
            return
        self._buffer.extend(self._decryptor.finalize())
        self._finalized = True

    def finish(self) -> None:
        while not self._finalized:
            self._fill()


def _encode_header(header: ArtifactHeader) -> bytes:
    payload = {
        "application_version": header.application_version,
        "backup_id": str(header.backup_id),
        "compression": header.compression.value,
        "created_at": header.created_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "database_revision": header.database_revision,
        "encryption": header.encryption.value,
        "nonce": b64encode(header.nonce).decode("ascii"),
        "plaintext_size": header.plaintext_size,
    }
    return json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _read_prefix_and_header(stream: BinaryIO) -> tuple[bytes, ArtifactHeader]:
    prefix = _read_exact(stream, _PREFIX.size)
    magic, version, header_size = _PREFIX.unpack(prefix)
    if magic != _MAGIC:
        raise ArtifactFormatError("artifact magic is invalid")
    if version != BACKUP_FORMAT_VERSION:
        raise ArtifactFormatError("artifact version is unsupported")
    if header_size <= 0 or header_size > _MAX_HEADER_BYTES:
        raise ArtifactFormatError("artifact header size is invalid")
    header_bytes = _read_exact(stream, header_size)
    try:
        payload = json.loads(header_bytes.decode("ascii"))
        if not isinstance(payload, dict):
            raise TypeError
        header = ArtifactHeader(
            backup_id=UUID(payload["backup_id"]),
            application_version=str(payload["application_version"]),
            database_revision=str(payload["database_revision"]),
            created_at=datetime.fromisoformat(
                str(payload["created_at"]).replace("Z", "+00:00")
            ),
            compression=Compression(payload["compression"]),
            encryption=EncryptionAlgorithm(payload["encryption"]),
            nonce=b64decode(str(payload["nonce"]).encode("ascii"), validate=True),
            plaintext_size=int(payload["plaintext_size"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ArtifactFormatError("artifact header is invalid") from error
    return prefix + header_bytes, header


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ArtifactFormatError("artifact is truncated")
    return value


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256-GCM key must contain exactly 32 bytes")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
