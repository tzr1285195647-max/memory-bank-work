"""腾讯云录音文件识别客户端。

只在后端读取访问密钥。使用云 API v3 签名直接请求，避免把任何凭据或签名逻辑
放入微信小程序。录音以 Base64 提交，适合当前本机演示（单文件上限 5 MiB）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import re
import subprocess
import tempfile
import time
import wave
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, settings

ASR_HOST = "asr.tencentcloudapi.com"
ASR_ENDPOINT = f"https://{ASR_HOST}"
ASR_SERVICE = "asr"
ASR_VERSION = "2019-06-14"
MAX_INLINE_AUDIO_BYTES = 5 * 1024 * 1024


class AsrError(RuntimeError):
    """可安全展示给用户的语音识别错误。"""

    def __init__(self, message: str, *, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class TranscriptionResult:
    status: str
    transcript: str = ""
    error: str = ""
    duration_seconds: float | None = None


def detect_audio_format(audio: bytes) -> str:
    """按文件内容而不是扩展名判断常见录音格式。

    微信开发者工具和部分 Android 设备偶尔会在请求 mp3 时实际产出
    WebM/Opus；上传文件名仍然是 .mp3，因此不能依赖扩展名。
    """

    if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "wav"
    if audio[:3] == b"ID3" or (
        len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0
    ):
        return "mp3"
    if audio[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if audio[:4] == b"OggS":
        return "ogg"
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "m4a"
    return "unknown"


def _convert_webm_to_wav(audio: bytes) -> bytes:
    """把 WebM/Opus 规范化为腾讯云稳定支持的 16kHz 单声道 WAV。"""

    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - 依赖缺失时提供可读错误
        raise AsrError(
            "服务器缺少录音格式转换组件，请重新安装后端依赖",
            code="AUDIO_CONVERTER_MISSING",
        ) from exc

    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 库安装损坏
        raise AsrError(
            "录音格式转换组件不可用，请重新安装后端依赖",
            code="AUDIO_CONVERTER_UNAVAILABLE",
        ) from exc

    with tempfile.TemporaryDirectory(prefix="memory-bank-asr-") as temporary_dir:
        input_path = Path(temporary_dir) / "recording.webm"
        output_path = Path(temporary_dir) / "recording.wav"
        input_path.write_bytes(audio)
        command = [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AsrError(
                "录音格式转换失败，请重新录制后再试",
                code="AUDIO_CONVERT_FAILED",
                retryable=True,
            ) from exc

        if completed.returncode != 0 or not output_path.exists():
            raise AsrError(
                "无法读取这段录音，请重新录制后再试",
                code="AUDIO_DECODE_FAILED",
            )

        converted = output_path.read_bytes()
        if len(converted) <= 44 or detect_audio_format(converted) != "wav":
            raise AsrError(
                "这段录音没有可识别的声音，请重新录制后再试",
                code="EMPTY_DECODED_AUDIO",
            )
        if not _wav_has_audible_signal(converted):
            raise AsrError(
                "没有采集到麦克风声音。开发者工具请开启电脑麦克风；真机请检查微信和手机系统的麦克风权限后重新录制",
                code="SILENT_AUDIO",
            )
        return converted


def _wav_has_audible_signal(audio: bytes) -> bool:
    """排除开发者工具生成的数字静音，避免把空声音提交云端。"""

    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getsampwidth() != 2:
                return True
            samples = array("h", source.readframes(source.getnframes()))
    except (EOFError, wave.Error):
        return True
    if not samples:
        return False
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    # 真正的数字静音通常峰值不足 2；阈值保持很低，避免误伤轻声讲述。
    return peak > 64 or rms > 16


def prepare_audio_for_asr(audio: bytes) -> bytes:
    """在提交云端前修正设备实际输出的容器格式。"""

    if not audio:
        raise AsrError("录音内容为空", code="EMPTY_AUDIO")
    if detect_audio_format(audio) == "webm":
        return _convert_webm_to_wav(audio)
    return audio


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _safe_error(code: str, message: str) -> str:
    known = {
        "AuthFailure.InvalidAuthorization": "腾讯云密钥无效，请检查 SecretId、SecretKey 和子用户权限",
        "FailedOperation.CheckAuthInfoFailed": "腾讯云鉴权失败，请检查 ASR 权限",
        "FailedOperation.UserNotRegistered": "腾讯云语音识别服务尚未开通",
        "FailedOperation.UserHasNoAmount": "腾讯云语音识别额度已用完",
        "FailedOperation.UserHasNoFreeAmount": "腾讯云免费识别额度已用完",
        "FailedOperation.ServiceIsolate": "腾讯云语音识别服务因账户状态不可用",
        "RequestLimitExceeded.UinLimitExceeded": "语音识别请求过于频繁，请稍后重试",
    }
    return known.get(code, message or "腾讯云语音识别请求失败")


def _plain_transcript(data: dict[str, Any]) -> str:
    details = data.get("ResultDetail") or []
    sentences = []
    for item in details:
        if not isinstance(item, dict):
            continue
        sentence = str(item.get("FinalSentence") or item.get("SliceSentence") or "").strip()
        if sentence:
            sentences.append(sentence)
    if sentences:
        return "".join(sentences).strip()

    lines = []
    for line in str(data.get("Result") or "").splitlines():
        # 基础结果可能以 [声道:开始,声道:结束] 开头，校对框只需要正文。
        cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "".join(lines).strip()


class TencentAsrClient:
    def __init__(self, config: Settings = settings, *, transport: httpx.BaseTransport | None = None) -> None:
        if not config.asr_enabled:
            raise AsrError("腾讯云语音识别密钥尚未配置", code="ASR_NOT_CONFIGURED")
        self.secret_id = config.tencentcloud_secret_id.strip()
        self.secret_key = config.tencentcloud_secret_key.strip()
        self.region = config.tencent_asr_region.strip()
        self.engine = config.tencent_asr_engine.strip()
        self.transport = transport

    def _headers(self, action: str, payload: bytes, timestamp: int) -> dict[str, str]:
        date = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
        content_type = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{content_type}\nhost:{ASR_HOST}\n"
        signed_headers = "content-type;host"
        canonical_request = "\n".join(
            ["POST", "/", "", canonical_headers, signed_headers, _sha256_hex(payload)]
        )
        scope = f"{date}/{ASR_SERVICE}/tc3_request"
        string_to_sign = "\n".join(
            ["TC3-HMAC-SHA256", str(timestamp), scope, _sha256_hex(canonical_request.encode("utf-8"))]
        )
        secret_date = _hmac_sha256(("TC3" + self.secret_key).encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, ASR_SERVICE)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": ASR_HOST,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": ASR_VERSION,
            "X-TC-Region": self.region,
        }

    def _call(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        timestamp = int(time.time())
        try:
            with httpx.Client(timeout=30.0, transport=self.transport) as client:
                response = client.post(ASR_ENDPOINT, content=payload, headers=self._headers(action, payload, timestamp))
                response.raise_for_status()
                envelope = response.json().get("Response", {})
        except (httpx.HTTPError, ValueError) as exc:
            raise AsrError("无法连接腾讯云语音识别，请检查网络后重试", code="ASR_NETWORK", retryable=True) from exc

        error = envelope.get("Error")
        if error:
            code = str(error.get("Code") or "")
            message = _safe_error(code, str(error.get("Message") or ""))
            retryable = code.startswith("RequestLimitExceeded") or code.startswith("InternalError")
            raise AsrError(message, code=code, retryable=retryable)
        return envelope

    def submit(self, audio: bytes) -> str:
        audio = prepare_audio_for_asr(audio)
        if len(audio) > MAX_INLINE_AUDIO_BYTES:
            raise AsrError("录音超过 5MB，当前本机转写模式无法提交", code="AUDIO_TOO_LARGE")
        response = self._call(
            "CreateRecTask",
            {
                "EngineModelType": self.engine,
                "ChannelNum": 1,
                "ResTextFormat": 2,
                "SourceType": 1,
                "Data": base64.b64encode(audio).decode("ascii"),
                "DataLen": len(audio),
                "ConvertNumMode": 1,
                "FilterDirty": 0,
                "FilterModal": 0,
            },
        )
        task_id = (response.get("Data") or {}).get("TaskId")
        if task_id is None:
            raise AsrError("腾讯云没有返回识别任务编号", code="MISSING_TASK_ID")
        return str(task_id)

    def query(self, task_id: str) -> TranscriptionResult:
        response = self._call("DescribeTaskStatus", {"TaskId": int(task_id)})
        data = response.get("Data") or {}
        status_value = int(data.get("Status", 0))
        if status_value == 2:
            transcript = _plain_transcript(data)
            if not transcript:
                return TranscriptionResult(status="failed", error="没有识别到清晰的人声")
            return TranscriptionResult(
                status="success",
                transcript=transcript,
                duration_seconds=data.get("AudioDuration"),
            )
        if status_value == 3:
            return TranscriptionResult(status="failed", error=str(data.get("ErrorMsg") or "语音识别失败"))
        return TranscriptionResult(status="processing" if status_value == 1 else "waiting")


def create_client(config: Settings = settings) -> TencentAsrClient:
    return TencentAsrClient(config)
