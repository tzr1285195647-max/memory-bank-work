"""演示配置测试：确保默认 P0 流程不会意外访问外网。"""

from __future__ import annotations

from backend.config import load_settings


def test_mock_mode_ignores_a_stale_llm_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("LLM_API_KEY", "stale-key-must-not-be-used")
    monkeypatch.setenv("MEMORY_BANK_DATA_DIR", str(tmp_path / "mock-data"))

    configured = load_settings()

    assert configured.agent_mode == "mock"
    assert configured.llm_enabled is False


def test_llm_mode_requires_an_explicit_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MODE", "llm")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("MEMORY_BANK_DATA_DIR", str(tmp_path / "llm-data"))

    without_key = load_settings()
    assert without_key.llm_enabled is False

    monkeypatch.setenv("LLM_API_KEY", "explicit-test-key")
    with_key = load_settings()
    assert with_key.llm_enabled is True


def test_tencent_asr_requires_both_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_PROVIDER", "tencent")
    monkeypatch.setenv("MEMORY_BANK_DATA_DIR", str(tmp_path / "asr-data"))
    monkeypatch.delenv("TENCENTCLOUD_SECRET_ID", raising=False)
    monkeypatch.delenv("TENCENTCLOUD_SECRET_KEY", raising=False)

    assert load_settings().asr_enabled is False

    monkeypatch.setenv("TENCENTCLOUD_SECRET_ID", "test-secret-id")
    assert load_settings().asr_enabled is False

    monkeypatch.setenv("TENCENTCLOUD_SECRET_KEY", "test-secret-key")
    configured = load_settings()
    assert configured.asr_enabled is True
    assert configured.tencent_asr_region == "ap-guangzhou"
    assert configured.tencent_asr_engine == "16k_zh_en_2.0"
