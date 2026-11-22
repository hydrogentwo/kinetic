"""Talking to the model.

One interface, three ways to answer it:

* ``OpenAI``  - any OpenAI-compatible server. Kinetic's ``kinetic serve`` is the
  intended one: the whole point is that the conversation never leaves the phone
  and the laptop.
* ``Fixture`` - canned answers from a JSON file. Lets the whole harness, the
  overlay and its tests run with no model at all, which is how the demo and the
  CI work.
* ``Auto``    - OpenAI if the endpoint answers, Fixture otherwise, and it says
  which one it picked every time so nothing is silently made up.

Every call that wants structure asks for schema-constrained JSON first
(``response_format: json_schema``). Kinetic supports that. If the server refuses
the schema or returns something that is not JSON, the call is retried once
without the constraint and the result is repaired leniently. A malformed model
answer must never become a wrong suggestion silently, so the caller always gets
either parsed data or a structured error.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import ModelConfig


class ModelError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    backend: str
    ms: int
    usage: dict[str, Any] | None = None


class Client:
    name = "base"

    def complete(self, messages: list[dict[str, str]], *, json_schema: dict[str, Any] | None = None,
                 max_tokens: int | None = None, temperature: float | None = None) -> Completion:
        raise NotImplementedError

    def structured(self, messages: list[dict[str, str]], schema: dict[str, Any], **kw) -> tuple[dict[str, Any], Completion]:
        comp = self.complete(messages, json_schema=schema, **kw)
        try:
            return extract_json(comp.text), comp
        except ValueError:
            # One retry, unconstrained, with the shape restated plainly.
            retry = messages + [
                {"role": "assistant", "content": comp.text[:2000]},
                {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object, no prose."},
            ]
            comp2 = self.complete(retry, json_schema=None, **kw)
            try:
                return extract_json(comp2.text), comp2
            except ValueError as e:
                raise ModelError(f"model did not return JSON: {e}") from e

    def health(self) -> tuple[bool, str]:
        return False, "not checked"


class Fixture(Client):
    """Canned answers, keyed by a rough shape of the request."""

    name = "fixture"

    def __init__(self, path: str | None = None):
        self.case: dict[str, Any] | None = None
        self.path = path
        if path:
            self.case = json.loads(open(path, encoding="utf-8").read())

    def complete(self, messages, *, json_schema=None, max_tokens=None, temperature=None) -> Completion:
        prompt = "\n".join(m.get("content", "") for m in messages)
        case = self.case or {}
        if "DRAFT ABOUT TO BE SENT" in prompt or "THE RULES ALREADY FLAGGED" in prompt:
            payload = _offline_verdict(prompt, case)
        elif case.get("by_app"):
            app = _app_in(prompt)
            by_app = case["by_app"]
            # A guard prompt has no app section of its own, so this is only
            # reached for readings; the per-app answers are readings.
            payload = by_app.get(app) or by_app.get("generic") or case["by_app"]["generic"]
        elif case.get("reading"):
            payload = case["reading"]
        else:
            payload = _canned(prompt)
        text = json.dumps(payload, ensure_ascii=False)
        return Completion(text=text, backend="fixture", ms=0, usage={"prompt_tokens": 0, "completion_tokens": 0})

    def health(self) -> tuple[bool, str]:
        return True, "fixture"


_APP_LINE = re.compile(r"^APP: .*\(([a-z_]+)\)", re.M)


def _app_in(prompt: str) -> str:
    """Which app the prompt is about, from the 'APP: WhatsApp (whatsapp).' line."""
    m = _APP_LINE.search(prompt)
    if m:
        return m.group(1)
    low = prompt.lower()
    for app in ("whatsapp", "discord", "slack", "sms", "linkedin"):
        if app in low:
            return app
    return "generic"


_RULE_LINE = re.compile(r"^- (?P<rule>[a-z_]+): (?P<detail>.+)$", re.M)


def _offline_verdict(prompt: str, case: dict[str, Any]) -> dict[str, Any]:
    """A verdict built from the rules that actually fired, not from a script.

    Without a model there is no judgement to offer, but the rules are real
    evidence about *this* draft, so the offline answer is those rules in the
    same shape a model would return. When nothing fired, it says so quietly
    instead of inventing a problem.
    """
    guards = case.get("guard_by_app") or {}
    canned = guards.get(_app_in(prompt)) or guards.get("generic") or {}
    rules = [(m["rule"], m["detail"]) for m in _RULE_LINE.finditer(prompt)]
    if not rules:
        return {
            "risk": 8,
            "why": "Nothing in this reads as a mistake. Send it." if canned else "Nothing stood out.",
            "reasons": [],
            "category": "fine",
            "fix": "",
            "alt": "",
        }
    detail = rules[0][1]
    # One plain sentence for the panel: the evidence, not the advice after it.
    why = re.split(r"(?<=[.!?])\s", detail)
    why = " ".join(why[:2])[:180]
    risk = min(88, 48 + 12 * len(rules))
    if canned.get("risk"):
        risk = max(risk, min(85, int(canned["risk"])))
    return {
        "risk": risk,
        # The first rule's evidence is the most specific thing that can be said.
        "why": why,
        "reasons": [detail for _rule, detail in rules][:3],
        "category": rules[0][0],
        "fix": canned.get("fix", "") if risk >= 45 else "",
        "alt": canned.get("alt", "") if risk >= 45 else "",
    }


def _canned(prompt: str) -> dict[str, Any]:
    """A defensible answer for any input, so the demo never shows an error."""
    last = ""
    for line in reversed(prompt.splitlines()):
        if line.startswith("Them:"):
            last = line[6:].strip()
            break
    return {
        "intent": {
            "situation": "Someone sent a message and you have not answered yet.",
            "what_they_want": "An answer, or at least an acknowledgement.",
            "what_you_want": "To reply without over-thinking it.",
            "stakes": "low",
            "open_questions": [],
            "noticed": ["No reply from you in the transcript"],
        },
        "options": [
            {
                "text": "yeah, sorry, been flat out. whats the ask?" if last else "hey, what's up?",
                "move": "answer",
                "tone": "neutral",
                "why": "Opens the door without promising anything.",
                "risk": 5,
                "risk_reasons": [],
            },
            {
                "text": "give me a bit and i'll come back to this properly",
                "move": "defer",
                "tone": "honest",
                "why": "Buys time honestly instead of going quiet.",
                "risk": 8,
                "risk_reasons": [],
            },
        ],
        "suggested_intents": ["just answer the question", "buy time without vanishing"],
    }


class OpenAI(Client):
    name = "openai"

    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self._model: str | None = None if cfg.model in ("", "auto") else cfg.model

    # -- plumbing ---------------------------------------------------------

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _req(self, path: str, body: dict[str, Any] | None = None, method: str | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self._url(path),
            data=data,
            method=method or ("POST" if body is not None else "GET"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
                "User-Agent": "second-thought/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise ModelError(f"{path} -> HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(f"cannot reach {self._url(path)}: {e}") from e

    def model_id(self) -> str:
        if self._model:
            return self._model
        try:
            data = self._req("/models")
            first = (data.get("data") or [{}])[0]
            self._model = str(first.get("id") or "model")
        except (ModelError, KeyError, IndexError, TypeError):
            self._model = "model"
        return self._model

    def health(self) -> tuple[bool, str]:
        try:
            data = self._req("/models")
            ids = [m.get("id") for m in data.get("data", [])]
            return True, f"{self.cfg.base_url} serving {', '.join(str(i) for i in ids) or 'unknown'}"
        except ModelError as e:
            return False, str(e)

    # -- completion -------------------------------------------------------

    def complete(self, messages, *, json_schema=None, max_tokens=None, temperature=None) -> Completion:
        body: dict[str, Any] = {
            "model": self.model_id(),
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "stream": False,
        }
        # Thinking models bury the answer in the reasoning on some builds; the
        # suggestion has to come back as a suggestion.
        body["chat_template_kwargs"] = {"enable_thinking": False}
        if json_schema and self.cfg.json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"schema": json_schema}}
        started = time.monotonic()
        try:
            data = self._req("/chat/completions", body)
        except ModelError:
            if json_schema and self.cfg.json_schema:
                # The server may not know the schema form; ask again unconstrained.
                self.cfg.json_schema = False
                return self.complete(messages, json_schema=None, max_tokens=max_tokens, temperature=temperature)
            raise
        try:
            choice = data["choices"][0]
            text = choice.get("message", {}).get("content") or ""
            if not text and choice.get("message", {}).get("reasoning_content"):
                text = choice["message"]["reasoning_content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"unexpected response shape: {str(data)[:300]}") from e
        ms = int((time.monotonic() - started) * 1000)
        return Completion(text=text, backend="openai", ms=ms, usage=data.get("usage"))


class Auto(Client):
    def __init__(self, cfg: ModelConfig, fixture_path: str | None = None):
        self.openai = OpenAI(cfg)
        self.fixture = Fixture(fixture_path)
        self.picked: Client | None = None
        self.reason = "not probed"

    def resolve(self) -> Client:
        if self.picked:
            return self.picked
        if self.openai.cfg.backend == "fixture":
            self.picked, self.reason = self.fixture, "backend is set to fixture"
            return self.picked
        ok, why = self.openai.health()
        self.picked = self.openai if ok else self.fixture
        self.reason = why
        self.name = self.picked.name
        return self.picked

    def complete(self, messages, **kw) -> Completion:
        return self.resolve().complete(messages, **kw)

    def health(self) -> tuple[bool, str]:
        c = self.resolve()
        return c.health()


def client(cfg: ModelConfig, fixture_path: str | None = None) -> Auto:
    return Auto(cfg, fixture_path)


# ---------------------------------------------------------------- JSON repair


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of whatever the model said."""
    if not text:
        raise ValueError("empty answer")
    candidates: list[str] = []
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1).strip())
    candidates.append(text.strip())
    # Slice from the first brace to the matching last brace.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            val = json.loads(c)
        except json.JSONDecodeError:
            try:
                val = json.loads(_tidy(c))
            except json.JSONDecodeError:
                continue
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val[0]
    raise ValueError(text[:200])


def _tidy(s: str) -> str:
    """Common model slips: trailing commas, smart quotes, unquoted keys."""
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', s)
    return s
