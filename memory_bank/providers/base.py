from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, BinaryIO, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable

from memory_bank.assets import StoredObject


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    structured: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class ASRResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str
    duration_ms: int
    model: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio: bytes = field(repr=False)
    media_type: str
    duration_ms: int
    voice: str
    model: str


@runtime_checkable
class ObjectStorage(Protocol):
    def put_stream(
        self,
        key: str,
        source: BinaryIO | Iterable[bytes],
        *,
        content_type: str,
        max_bytes: int | None = None,
        overwrite: bool = False,
    ) -> StoredObject: ...

    def open(self, key: str) -> BinaryIO: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> StoredObject: ...

    def delete(self, key: str) -> bool: ...


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        response_schema: Mapping[str, Any] | None = None,
    ) -> LLMResult: ...


@runtime_checkable
class ASRProvider(Protocol):
    name: str

    def transcribe(
        self,
        audio: bytes,
        *,
        media_type: str,
        language: str = "zh",
    ) -> ASRResult: ...


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "default",
        audio_format: str = "wav",
    ) -> TTSResult: ...


# Explicit aliases make naming unambiguous for future provider registries.
ObjectStorageProvider = ObjectStorage

