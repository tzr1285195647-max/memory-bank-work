from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from memory_bank.assets import normalize_media_type, sniff_media_type
from memory_bank.providers.base import (
    ASRResult,
    ChatMessage,
    LLMResult,
    TranscriptSegment,
    TTSResult,
)


class MockLLMProvider:
    """Deterministic drop-in used for local development and tests."""

    name = "mock-llm"

    def __init__(
        self,
        responses: Sequence[str | Mapping[str, Any]] | None = None,
        *,
        response_factory: Callable[[Sequence[ChatMessage]], str | Mapping[str, Any]] | None = None,
    ) -> None:
        self._responses = deque(responses or ())
        self._response_factory = response_factory

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        response_schema: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        del temperature, response_schema
        if self._response_factory is not None:
            value = self._response_factory(messages)
        elif self._responses:
            value = self._responses.popleft()
        else:
            prompt = next((message.content for message in reversed(messages) if message.role == "user"), "")
            value = f"[Mock] {prompt}".strip()

        structured: Mapping[str, Any] | None = None
        if isinstance(value, Mapping):
            structured = dict(value)
            text = json.dumps(structured, ensure_ascii=False, sort_keys=True)
        else:
            text = str(value)
        input_tokens = sum(max(1, len(message.content) // 4) for message in messages)
        return LLMResult(
            text=text,
            model=self.name,
            input_tokens=input_tokens,
            output_tokens=max(1, len(text) // 4),
            structured=structured,
        )


class MockASRProvider:
    name = "mock-asr"

    def __init__(self, transcript: str = "这是一段用于本地开发的模拟转写。") -> None:
        self.transcript = transcript.strip()

    def transcribe(
        self,
        audio: bytes,
        *,
        media_type: str,
        language: str = "zh",
    ) -> ASRResult:
        declared = normalize_media_type(media_type)
        detected = sniff_media_type(audio[:64])
        if detected is None or detected != declared or not declared.startswith("audio/"):
            raise ValueError("mock ASR received invalid or mismatched audio data")
        # Stable, content-dependent timing makes fixture outputs reproducible.
        duration_ms = max(500, min(60_000, len(audio) * 2))
        segment = TranscriptSegment(
            index=0,
            start_ms=0,
            end_ms=duration_ms,
            text=self.transcript,
            confidence=1.0,
        )
        return ASRResult(
            text=self.transcript,
            segments=(segment,),
            language=language,
            duration_ms=duration_ms,
            model=self.name,
        )


class MockTTSProvider:
    """Create a short valid PCM WAV tone without network or model files."""

    name = "mock-tts"

    def __init__(self, *, sample_rate: int = 8_000) -> None:
        self.sample_rate = sample_rate

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "default",
        audio_format: str = "wav",
    ) -> TTSResult:
        if not text.strip():
            raise ValueError("text must not be empty")
        if audio_format.lower() not in {"wav", "wave"}:
            raise ValueError("mock TTS only supports wav")

        duration_ms = max(250, min(2_000, len(text) * 60))
        frame_count = self.sample_rate * duration_ms // 1_000
        frequency = 330 + (int(hashlib.sha256(voice.encode("utf-8")).hexdigest()[:2], 16) % 120)
        amplitude = 2_500
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                sample = int(amplitude * math.sin(2 * math.pi * frequency * index / self.sample_rate))
                frames.extend(struct.pack("<h", sample))
            wav.writeframes(bytes(frames))

        return TTSResult(
            audio=output.getvalue(),
            media_type="audio/wav",
            duration_ms=duration_ms,
            voice=voice,
            model=self.name,
        )

