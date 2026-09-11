import json
import logging

from memory_bank.observability import JsonFormatter, redact


def test_redact_nested_secrets_and_bearer_tokens():
    value = {
        "password": "secret",
        "nested": {"api_key": "sk-demo", "note": "Bearer abc.def.ghi"},
    }
    cleaned = redact(value)
    assert cleaned["password"] == "[REDACTED]"
    assert cleaned["nested"]["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["note"] == "Bearer [REDACTED]"


def test_json_formatter_includes_context_without_secret():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "created", (), None)
    record.context = {"project_id": "p1", "access_token": "hidden"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "created"
    assert payload["context"]["project_id"] == "p1"
    assert payload["context"]["access_token"] == "[REDACTED]"

