from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator


class AssetError(ValueError):
    """Base class for asset validation and storage failures."""


class InvalidObjectKeyError(AssetError):
    pass


class AssetTooLargeError(AssetError):
    pass


class UnsupportedMediaTypeError(AssetError):
    pass


class MediaTypeMismatchError(AssetError):
    pass


class EmptyAssetError(AssetError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str
    media_type: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "StoredObject":
        return cls(
            key=str(data["key"]),
            size_bytes=int(data["size_bytes"]),
            sha256=str(data["sha256"]),
            media_type=str(data["media_type"]),
            created_at=str(data["created_at"]),
        )


# Only formats needed by the first product version are accepted.  Adding a MIME
# type must also add a reliable signature below; filename extensions are never
# trusted for validation.
DEFAULT_ALLOWED_MEDIA_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/wav",
        "audio/ogg",
        "audio/mp4",
        "audio/webm",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

_MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-m4a": "audio/mp4",
    "image/jpg": "image/jpeg",
}


def normalize_media_type(value: str) -> str:
    media_type = value.split(";", 1)[0].strip().lower()
    return _MIME_ALIASES.get(media_type, media_type)


def sniff_media_type(header: bytes) -> str | None:
    """Return a MIME type based on magic bytes, never on a filename."""
    if len(header) >= 12 and header.startswith(b"RIFF"):
        container = header[8:12]
        if container == b"WAVE":
            return "audio/wav"
        if container == b"WEBP":
            return "image/webp"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm"
    # ISO Base Media (M4A/MP4).  The box size precedes the ftyp marker.
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "audio/mp4"
    if header.startswith(b"ID3"):
        return "audio/mpeg"
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None


def _iter_chunks(source: BinaryIO | Iterable[bytes], chunk_size: int) -> Iterator[bytes]:
    reader = getattr(source, "read", None)
    if callable(reader):
        while True:
            chunk = reader(chunk_size)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("asset stream must yield bytes")
            yield bytes(chunk)
        return

    for chunk in source:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("asset iterable must yield bytes")
        if chunk:
            yield bytes(chunk)


class LocalObjectStorage:
    """Small, immutable local object store with streaming validation.

    Objects are addressed by caller-provided logical keys.  Keys are always
    resolved below ``root`` and cannot use traversal, absolute paths, Windows
    separators, or the store's reserved internal directories.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        allowed_media_types: Iterable[str] = DEFAULT_ALLOWED_MEDIA_TYPES,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._temp_dir = self.root / ".tmp"
        self._metadata_dir = self.root / ".metadata"
        self._temp_dir.mkdir(exist_ok=True)
        self._metadata_dir.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size
        self.allowed_media_types = frozenset(normalize_media_type(item) for item in allowed_media_types)

    @staticmethod
    def validate_key(key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 1_024 or "\x00" in key or "\\" in key:
            raise InvalidObjectKeyError("invalid object key")
        if key != key.strip():
            raise InvalidObjectKeyError("object key cannot have surrounding whitespace")
        pure = PurePosixPath(key)
        parts = pure.parts
        if pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
            raise InvalidObjectKeyError("object key must be a relative normalized path")
        if parts[0] in {".tmp", ".metadata"}:
            raise InvalidObjectKeyError("object key uses a reserved or absolute path")
        windows_devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        for part in parts:
            # Colons permit NTFS alternate data streams, while device names
            # such as NUL remain special even when followed by an extension.
            device_stem = part.split(".", 1)[0].upper()
            if len(part) > 255 or ":" in part or part.endswith((" ", ".")) or device_stem in windows_devices:
                raise InvalidObjectKeyError("object key contains an unsafe path component")
        return pure.as_posix()

    def _path_for(self, key: str) -> Path:
        normalized = self.validate_key(key)
        target = (self.root / Path(*PurePosixPath(normalized).parts)).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise InvalidObjectKeyError("object key escapes storage root") from exc
        if target == self.root:
            raise InvalidObjectKeyError("object key cannot address storage root")
        return target

    def _metadata_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._metadata_dir / f"{digest}.json"

    def put_stream(
        self,
        key: str,
        source: BinaryIO | Iterable[bytes],
        *,
        content_type: str,
        max_bytes: int | None = None,
        overwrite: bool = False,
    ) -> StoredObject:
        target = self._path_for(key)
        declared_type = normalize_media_type(content_type)
        if declared_type not in self.allowed_media_types:
            raise UnsupportedMediaTypeError(f"media type is not allowed: {declared_type or '<empty>'}")

        limit = self.max_bytes if max_bytes is None else min(self.max_bytes, max_bytes)
        if limit <= 0:
            raise ValueError("max_bytes must be positive")

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self._temp_dir, prefix="upload-", delete=False) as temp:
                temp_path = Path(temp.name)
                digest = hashlib.sha256()
                total = 0
                header = bytearray()
                for chunk in _iter_chunks(source, self.chunk_size):
                    total += len(chunk)
                    if total > limit:
                        raise AssetTooLargeError(f"asset exceeds {limit} bytes")
                    if len(header) < 64:
                        header.extend(chunk[: 64 - len(header)])
                    digest.update(chunk)
                    temp.write(chunk)
                temp.flush()
                os.fsync(temp.fileno())

            if total == 0:
                raise EmptyAssetError("empty assets are not accepted")
            detected_type = sniff_media_type(bytes(header))
            if detected_type is None:
                raise UnsupportedMediaTypeError("file signature is not recognized")
            if detected_type != declared_type:
                raise MediaTypeMismatchError(
                    f"declared media type {declared_type!r} does not match file signature {detected_type!r}"
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            # Objects are immutable by default.  This also prevents silently
            # replacing a user asset when an idempotency key is reused badly.
            if target.exists() and not overwrite:
                raise FileExistsError(key)
            if target.is_symlink():
                raise InvalidObjectKeyError("object target cannot be a symbolic link")
            os.replace(temp_path, target)
            temp_path = None

            stored = StoredObject(
                key=self.validate_key(key),
                size_bytes=total,
                sha256=digest.hexdigest(),
                media_type=detected_type,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            metadata_path = self._metadata_path(stored.key)
            metadata_temp = metadata_path.with_suffix(".tmp")
            metadata_temp.write_text(json.dumps(stored.to_dict(), ensure_ascii=False), encoding="utf-8")
            os.replace(metadata_temp, metadata_path)
            return stored
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        max_bytes: int | None = None,
        overwrite: bool = False,
    ) -> StoredObject:
        return self.put_stream(
            key,
            io.BytesIO(data),
            content_type=content_type,
            max_bytes=max_bytes,
            overwrite=overwrite,
        )

    def open(self, key: str) -> BinaryIO:
        return self._path_for(key).open("rb")

    def read_bytes(self, key: str) -> bytes:
        with self.open(key) as stream:
            return stream.read()

    def exists(self, key: str) -> bool:
        target = self._path_for(key)
        return target.is_file() and not target.is_symlink()

    def stat(self, key: str) -> StoredObject:
        target = self._path_for(key)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(key)
        metadata_path = self._metadata_path(self.validate_key(key))
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            stored = StoredObject.from_dict(data)
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            # Metadata can be rebuilt safely from the object itself.
            digest = hashlib.sha256()
            header = bytearray()
            size = 0
            with target.open("rb") as stream:
                for chunk in _iter_chunks(stream, self.chunk_size):
                    size += len(chunk)
                    if len(header) < 64:
                        header.extend(chunk[: 64 - len(header)])
                    digest.update(chunk)
            media_type = sniff_media_type(bytes(header))
            if media_type is None:
                raise UnsupportedMediaTypeError("stored object has an unrecognized signature")
            stored = StoredObject(
                key=self.validate_key(key),
                size_bytes=size,
                sha256=digest.hexdigest(),
                media_type=media_type,
                created_at=datetime.fromtimestamp(target.stat().st_mtime, UTC).isoformat(timespec="seconds"),
            )
        return stored

    def delete(self, key: str) -> bool:
        normalized = self.validate_key(key)
        target = self._path_for(normalized)
        if target.is_symlink():
            raise InvalidObjectKeyError("refusing to delete a symbolic-link object")
        try:
            target.unlink()
        except FileNotFoundError:
            deleted = False
        else:
            deleted = True
        self._metadata_path(normalized).unlink(missing_ok=True)
        # Remove empty object subdirectories, but never internal/store roots.
        parent = target.parent
        while parent not in {self.root, self._temp_dir, self._metadata_dir}:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return deleted
