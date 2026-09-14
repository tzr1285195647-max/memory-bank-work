"""腾讯云 ASR 客户端测试：使用内存传输，不访问网络、不消耗额度。"""

from __future__ import annotations

import json

import httpx

from backend.asr import TencentAsrClient, detect_audio_format, prepare_audio_for_asr
from backend.config import Settings


def asr_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        objects_dir=tmp_path,
        database_url="sqlite://",
        jwt_secret="test",
        jwt_issuer="test",
        access_token_minutes=1,
        max_upload_bytes=1024,
        host="127.0.0.1",
        port=8787,
        tencentcloud_secret_id="test-id",
        tencentcloud_secret_key="test-key",
    )


def test_submit_uses_signed_request_without_exposing_secret(tmp_path):
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        observed["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"Response": {"Data": {"TaskId": 12345}, "RequestId": "r1"}})

    client = TencentAsrClient(asr_settings(tmp_path), transport=httpx.MockTransport(handler))
    assert client.submit(b"ID3-audio") == "12345"
    assert observed["body"]["SourceType"] == 1
    assert observed["body"]["DataLen"] == 9
    assert observed["body"]["EngineModelType"] == "16k_zh_en_2.0"
    assert "test-key" not in observed["authorization"]


def test_audio_format_detection_uses_real_content():
    assert detect_audio_format(b"ID3-not-really-a-full-file") == "mp3"
    assert detect_audio_format(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"
    assert detect_audio_format(b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm") == "webm"


def test_submit_normalizes_webm_to_wav_before_upload(tmp_path, monkeypatch):
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json={"Response": {"Data": {"TaskId": 7}, "RequestId": "r-webm"}})

    webm = b"\x1aE\xdf\xa3fake-webm"
    wav = b"RIFF\x00\x00\x00\x00WAVE" + b"normalized"
    monkeypatch.setattr("backend.asr._convert_webm_to_wav", lambda audio: wav)

    client = TencentAsrClient(asr_settings(tmp_path), transport=httpx.MockTransport(handler))
    assert client.submit(webm) == "7"
    assert observed["body"]["DataLen"] == len(wav)
    assert prepare_audio_for_asr(b"ID3-mp3") == b"ID3-mp3"


def test_silent_webm_is_rejected_before_cloud_submission(monkeypatch):
    from backend.asr import AsrError

    monkeypatch.setattr(
        "backend.asr._convert_webm_to_wav",
        lambda _audio: (_ for _ in ()).throw(AsrError("没有采集到麦克风声音", code="SILENT_AUDIO")),
    )
    try:
        prepare_audio_for_asr(b"\x1aE\xdf\xa3silent")
    except AsrError as exc:
        assert exc.code == "SILENT_AUDIO"
    else:
        raise AssertionError("静音录音不应继续提交云端")


def test_query_returns_plain_transcript(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Response": {
                    "Data": {
                        "Status": 2,
                        "Result": "[0:0.000,0:1.200] 那年秋天。\n[0:1.200,0:2.000] 桂花开了。",
                        "AudioDuration": 2.0,
                    },
                    "RequestId": "r2",
                }
            },
        )

    client = TencentAsrClient(asr_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = client.query("12345")
    assert result.status == "success"
    assert result.transcript == "那年秋天。桂花开了。"
