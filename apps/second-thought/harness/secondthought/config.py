"""Configuration for Second Thought.

Everything lives in one JSON file (``~/.second-thought/config.json`` by default,
or ``$SECOND_THOUGHT_HOME``). It is written with comments-as-text: the example
written by ``second-thought init`` explains every field in its string values.

The three sections mirror the three things the person decides for themselves:

* ``model``  where the reading happens (a local ``kinetic serve``).
* ``watch``  which apps the harness is allowed to read, and when.
* ``intent`` what the user is trying to do, in their own words. This is the
  "options to add intentions" the overlay exposes.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def home() -> Path:
    return Path(os.environ.get("SECOND_THOUGHT_HOME", Path.home() / ".second-thought"))


@dataclass
class ModelConfig:
    # An OpenAI-compatible endpoint. Kinetic's own server is the default; any
    # endpoint that speaks the same API works, including a hosted one, but the
    # point of the thing is that the conversation never leaves the machine.
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "auto"
    api_key: str = "local"
    backend: str = "openai"  # "openai" or "fixture" (offline demo, no model)
    timeout_s: float = 120.0
    temperature: float = 0.35
    max_tokens: int = 900
    json_schema: bool = True  # ask for schema-constrained JSON, fall back if refused
    # Two smaller calls instead of one large one: the guard has to answer in
    # under a second while the person is typing, the reading can take longer.
    split_calls: bool = True


@dataclass
class GuardConfig:
    enabled: bool = True
    # "nudge"  - show a quiet note next to the send button, never blocks.
    # "hold"   - also mark high-risk drafts red and require a second tap.
    # "off"    - no feedback on drafts at all.
    strength: str = "nudge"
    min_risk: int = 35  # below this, say nothing. Silence is a feature.
    min_chars: int = 4
    debounce_ms: int = 700
    # The harness never presses send, and never edits the text field. It only
    # offers a rewrite to copy. This is enforced in the Android app, not here,
    # but it is stated in the config so nobody has to read the Kotlin to learn it.
    never_send: bool = True


@dataclass
class WatchConfig:
    enabled: bool = True
    # Apps the harness may read. Everything else is ignored, including screen
    # changes, because the Android app checks the package name before it reads a
    # single node. Remove an entry to make that app invisible to the harness.
    apps: list[str] = field(
        default_factory=lambda: [
            "whatsapp", "discord", "sms", "slack", "instagram", "telegram",
            "messenger", "signal", "linkedin",
        ]
    )
    # The transcript sent to the model is capped so a long Discord server does
    # not turn into a 40 000-token request every time someone types.
    max_turns: int = 40
    max_chars: int = 6000
    # Messages from these names are always kept, even when the cap trims.
    keep_senders: list[str] = field(default_factory=list)
    include_own_draft: bool = True


@dataclass
class IntentConfig:
    # What the person is trying to do right now, in their words. The overlay has
    # an "intent" button that appends here without a restart.
    goals: list[str] = field(
        default_factory=lambda: [
            "Keep it short and warm. I over-explain when I am nervous.",
            "Do not promise anything about time or money that I have not decided.",
            "If someone is upset, acknowledge the feeling before solving anything.",
        ]
    )
    # Their voice. Used on every suggestion; the model is told to imitate the
    # register, not to invent a personality.
    voice: str = (
        "Lowercase is fine. Dry, plain, a little blunt. No emoji unless the other "
        "person used one in the last two messages. No exclamation marks in a row. "
        "I do not use 'haha' or 'lol'."
    )
    # Things the guard should always catch, on top of its own judgement.
    avoid: list[str] = field(
        default_factory=lambda: [
            "double texting someone who has not answered",
            "apologising for something I have already apologised for",
            "anything that sounds like an ultimatum when I am angry",
            "sending a wall of text to a thread that has been one-liners",
        ]
    )
    # Per-person notes, keyed by the name as it appears in the transcript.
    people: dict[str, str] = field(default_factory=dict)
    # The natural-language instruction from the phone's "tell it what to say"
    # sheet lands here at request time; it is not persisted on its own.
    thread_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    # A pairing token is required as soon as the harness listens on anything but
    # loopback. `second-thought serve --lan` generates one and prints it as a QR.
    token: str = ""
    # Kept for the overlay's history view; SQLite, local only.
    store_history: bool = True


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    @staticmethod
    def path() -> Path:
        return home() / "config.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or cls.path()
        raw: dict[str, Any] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            model=_merge(ModelConfig, raw.get("model")),
            guard=_merge(GuardConfig, raw.get("guard")),
            watch=_merge(WatchConfig, raw.get("watch")),
            intent=_merge(IntentConfig, raw.get("intent")),
            server=_merge(ServerConfig, raw.get("server")),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)  # it contains people's names and what you are like
        except OSError:
            pass
        return path

    @property
    def db_path(self) -> Path:
        return home() / "second-thought.db"


def _merge(cls: type, raw: Any):
    if not isinstance(raw, dict):
        return cls()
    known = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in raw.items() if k in known})


EXAMPLE = Config()


def ensure_init() -> Path:
    path = Config.path()
    if not path.exists():
        EXAMPLE.save(path)
    return path
