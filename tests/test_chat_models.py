import io
import json
import socket
import urllib.error

import pytest

from backcountry.chat.models import (DEFAULT_LLM, FakeModel, ModelBadOutput, ModelTimeout, ModelUnavailable,
                                     OllamaModel, model_from_env, model_from_spec, read_env, shape_problem)

SCHEMA = {"type": "object", "required": ["reply", "kind"], "additionalProperties": False,
          "properties": {"reply": {"type": "string"}, "kind": {"type": "string", "enum": ["change", "explain"]},
                         "span": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                         "cap": {"type": ["number", "null"]}}}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(*contents, record=None):
    """An urlopen stand-in that answers each call with the next Ollama /api/chat body."""
    answers = list(contents)

    def open_(req, timeout):
        if record is not None:
            record.append({"url": req.full_url, "body": json.loads(req.data), "timeout": timeout})
        item = answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(json.dumps({"message": {"role": "assistant", "content": item}}).encode())
    return open_


def test_shape_problem_checks_type_enum_required_extra_and_arrays():
    assert shape_problem({"reply": "hi", "kind": "change"}, SCHEMA) is None
    assert shape_problem({"reply": "hi"}, SCHEMA) == "answer.kind is missing"
    assert shape_problem({"reply": "hi", "kind": "nope"}, SCHEMA) == "answer.kind should be one of change, explain"
    assert shape_problem({"reply": 3, "kind": "change"}, SCHEMA) == "answer.reply should be string"
    assert shape_problem({"reply": "hi", "kind": "change", "x": 1}, SCHEMA) == "answer.x is not allowed"
    assert shape_problem({"reply": "", "kind": "change", "span": [1]}, SCHEMA) == "answer.span should have 2 items"
    assert shape_problem({"reply": "", "kind": "change", "span": [1, "a"]}, SCHEMA) == "answer.span[1] should be number"
    assert shape_problem({"reply": "", "kind": "change", "cap": None}, SCHEMA) is None
    assert shape_problem({"reply": "", "kind": "change", "cap": True}, SCHEMA) == "answer.cap should be number or null"


def test_ollama_sends_the_schema_and_settings_and_returns_the_parsed_answer():
    sent = []
    m = OllamaModel("qwen3:14b", "http://127.0.0.1:11434/", opener=opener_returning('{"reply": "Hi", "kind": "explain"}', record=sent))
    assert m.name == "ollama:qwen3:14b"
    assert m.complete("rules", [{"role": "user", "content": "hello"}], SCHEMA) == {"reply": "Hi", "kind": "explain"}
    body = sent[0]["body"]
    assert sent[0]["url"] == "http://127.0.0.1:11434/api/chat" and sent[0]["timeout"] == 60.0
    assert body["model"] == "qwen3:14b" and body["format"] == SCHEMA and body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 8192}
    assert body["messages"] == [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}]


def test_ollama_retries_once_with_the_problem_then_gives_up():
    sent = []
    m = OllamaModel("x", opener=opener_returning("not json", '{"reply": "ok", "kind": "explain"}', record=sent))
    assert m.complete("s", [{"role": "user", "content": "q"}], SCHEMA)["reply"] == "ok"
    assert "invalid" in sent[1]["body"]["messages"][-1]["content"]
    bad = OllamaModel("x", opener=opener_returning('{"reply": 1}', '{"kind": "zzz"}'))
    with pytest.raises(ModelBadOutput, match="didn't make sense"):
        bad.complete("s", [], SCHEMA)


def test_ollama_errors_become_user_facing_messages():
    refused = OllamaModel("x", opener=opener_returning(urllib.error.URLError(ConnectionRefusedError(61, "refused"))))
    with pytest.raises(ModelUnavailable, match=r"isn't reachable \(Ollama at 127.0.0.1:11434\). Start Ollama"):
        refused.complete("s", [], SCHEMA)
    slow = OllamaModel("x", opener=opener_returning(socket.timeout("timed out")))
    with pytest.raises(ModelTimeout, match="took longer than 60 s"):
        slow.complete("s", [], SCHEMA)
    missing = OllamaModel("nope:1b", opener=opener_returning(
        urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b'{"error":"model not found"}'))))
    with pytest.raises(ModelUnavailable, match="ollama pull nope:1b"):
        missing.complete("s", [], SCHEMA)


def test_fake_model_replays_answers_and_records_calls():
    m = FakeModel([{"reply": "a", "kind": "explain"}, ModelTimeout("slow")])
    assert m.complete("s", [{"role": "user", "content": "q"}], SCHEMA)["reply"] == "a"
    with pytest.raises(ModelTimeout):
        m.complete("s", [], SCHEMA)
    assert len(m.calls) == 2 and m.calls[0]["system"] == "s"


def test_env_file_and_environment_choose_the_model(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nBACKCOUNTRY_LLM="ollama:gemma4:12b"\n\nOLLAMA_URL=http://10.0.0.2:11434\n')
    env = read_env(env_file, environ={})
    assert env == {"BACKCOUNTRY_LLM": "ollama:gemma4:12b", "OLLAMA_URL": "http://10.0.0.2:11434"}
    assert read_env(env_file, environ={"BACKCOUNTRY_LLM": "ollama:llama3.2"})["BACKCOUNTRY_LLM"] == "ollama:llama3.2"
    m = model_from_env(env)
    assert isinstance(m, OllamaModel) and m.name == "ollama:gemma4:12b" and m.url == "http://10.0.0.2:11434"
    assert model_from_env({}).name == DEFAULT_LLM
    assert read_env(tmp_path / "missing.env", environ={}) == {}
    with pytest.raises(ValueError, match="ollama:<model>"):
        model_from_spec("gpt:4")
