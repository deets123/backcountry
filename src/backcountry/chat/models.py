"""The chat model behind one small interface: a local model through Ollama now, Claude later.

Only the Python standard library is used (urllib), so the chat adds no dependency. Settings come from the
environment or a git-ignored `.env` at the repo root: BACKCOUNTRY_LLM=ollama:<model>, OLLAMA_URL.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

from backcountry.region import REPO_ROOT

DEFAULT_LLM = "ollama:qwen3:14b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
TIMEOUT_S = 60.0
NUM_CTX = 8192  # Ollama's default 4,096-token window is too small for a fact sheet plus a few messages


class ModelError(Exception):
    """A chat-model failure; the message is shown to the user as is."""


class ModelUnavailable(ModelError):
    pass


class ModelTimeout(ModelError):
    pass


class ModelBadOutput(ModelError):
    pass


class ChatModel(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], schema: dict, timeout_s: float = TIMEOUT_S) -> dict: ...


def _is(value, t: str) -> bool:
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, {"string": str, "object": dict, "array": list, "boolean": bool, "null": type(None)}[t])


def shape_problem(value, schema: dict, path: str = "answer") -> str | None:
    """The first way `value` breaks the (small) JSON schema, or None. Covers type, enum, required,
    additionalProperties, properties, items, minItems/maxItems: what the chat's answer shape uses."""
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in types):
            return f"{path} should be {' or '.join(types)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} should be one of {', '.join(str(v) for v in schema['enum'] if v is not None)}"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is missing"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = [k for k in value if k not in props]
            if extra:
                return f"{path}.{extra[0]} is not allowed"
        for key, sub in props.items():
            if key in value and (problem := shape_problem(value[key], sub, f"{path}.{key}")):
                return problem
    if isinstance(value, list):
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if (lo is not None and len(value) < lo) or (hi is not None and len(value) > hi):
            return f"{path} should have {lo if lo == hi else f'{lo}-{hi}'} items"
        if "items" in schema:
            for i, item in enumerate(value):
                if problem := shape_problem(item, schema["items"], f"{path}[{i}]"):
                    return problem
    return None


class OllamaModel:
    def __init__(self, model: str, url: str = DEFAULT_OLLAMA_URL, opener: Callable = urllib.request.urlopen):
        self.model, self.url, self._open = model, url.rstrip("/"), opener
        self.name = f"ollama:{model}"

    def _post(self, payload: dict, timeout_s: float) -> dict:
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json"})
        where = self.url.split("//", 1)[-1]
        slow = ModelTimeout(f"The chat model ({self.name}) took longer than {timeout_s:.0f} s. Try again, or use a smaller model.")
        try:
            with self._open(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:  # before URLError: HTTPError is a URLError
            if e.code == 404:
                raise ModelUnavailable(f"Ollama doesn't have the model {self.model}. Run `ollama pull {self.model}`, "
                                       "or pick another model in .env.") from e
            raise ModelBadOutput(f"Ollama answered {e.code}: {e.read().decode(errors='replace')[:200]}") from e
        except (TimeoutError, socket.timeout) as e:
            raise slow from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise slow from e
            raise ModelUnavailable(f"The chat model isn't reachable (Ollama at {where}). Start Ollama, or plan with the form.") from e
        except json.JSONDecodeError as e:
            raise ModelBadOutput("Ollama sent something that isn't JSON.") from e

    def complete(self, system: str, messages: list[dict], schema: dict, timeout_s: float = TIMEOUT_S) -> dict:
        msgs = [{"role": "system", "content": system}, *messages]
        for _ in range(2):  # one retry, with the problem fed back
            out = self._post({"model": self.model, "messages": msgs, "format": schema, "stream": False, "think": False,
                              "options": {"temperature": 0, "num_ctx": NUM_CTX}}, timeout_s)
            text = (out.get("message") or {}).get("content", "")
            try:
                answer = json.loads(text)
                problem = shape_problem(answer, schema)
            except json.JSONDecodeError as e:
                answer, problem = None, f"it is not JSON ({e.msg})"
            if problem is None:
                return answer
            msgs = [*msgs, {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That answer was invalid: {problem}. Reply again with only JSON in the required shape."}]
        raise ModelBadOutput("The model's answer didn't make sense; try rephrasing.")


class FakeModel:
    """Scripted answers for tests: each call returns the next item, or raises it if it is an exception."""

    def __init__(self, answers: list, name: str = "fake"):
        self.answers, self.name, self.calls = list(answers), name, []

    def complete(self, system: str, messages: list[dict], schema: dict, timeout_s: float = TIMEOUT_S) -> dict:
        self.calls.append({"system": system, "messages": messages, "schema": schema})
        if not self.answers:
            raise ModelBadOutput("The fake model has no more answers.")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def read_env(path: Path = REPO_ROOT / ".env", environ: dict | None = None) -> dict:
    """KEY=VALUE lines from a .env file (blank lines and # comments ignored); the real environment wins."""
    environ = dict(os.environ) if environ is None else environ
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("BACKCOUNTRY_LLM", "OLLAMA_URL", *out):
        if key in environ:
            out[key] = environ[key]
    return out


def model_from_spec(spec: str, env: dict | None = None) -> ChatModel:
    provider, _, name = spec.partition(":")
    if provider == "ollama" and name:
        return OllamaModel(name, (env or {}).get("OLLAMA_URL") or DEFAULT_OLLAMA_URL)
    raise ValueError(f"Unknown chat model {spec!r}: use ollama:<model>, e.g. ollama:qwen3:14b (Claude support comes later).")


def model_from_env(env: dict | None = None) -> ChatModel:
    env = read_env() if env is None else env
    return model_from_spec(env.get("BACKCOUNTRY_LLM") or DEFAULT_LLM, env)
