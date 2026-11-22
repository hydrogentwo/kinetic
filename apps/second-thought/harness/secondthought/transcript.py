"""Reading a conversation off a screen.

The phone hands over what it can see: a flat list of text nodes, each with the
text, sometimes a speaker label, sometimes a bounding box, and the app it came
from. That is not a conversation yet. This module turns it into one:

* which lines are messages and which are chrome ("Today", "Online", "typing…")
* which messages are mine and which are theirs
* where one message ends and the next begins, when a bubble wraps over lines
* how long the other person has been quiet, and how many times I have now sent
  something into that quiet

Everything here is deterministic and offline. It is the part that has to be
right, because everything downstream reasons about what this returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .apps import AppProfile, profile

# ---------------------------------------------------------------- chrome

# Lines that are app furniture rather than speech. Matched case-insensitively
# against the whole line, because "Today" as a message body is possible but rare
# and misreading one is cheaper than reading "Today" as something someone said.
_CHROME_EXACT = {
    "today", "yesterday", "tomorrow", "online", "offline", "last seen recently",
    "typing", "typing…", "typing...", "voice message", "photo", "video", "gif",
    "sticker", "contact", "location", "audio", "document", "missed voice call",
    "missed video call", "message", "messages", "sent", "delivered", "read",
    "seen", "reply", "forward", "delete", "copy", "edited", "pinned message",
    "encrypted", "end-to-end encrypted", "type a message", "message…",
    "message...", "search", "new message", "unread messages",
    "jump to present", "react", "add reaction", "more", "cancel", "send",
}

# "12:04", "12:04 PM", "Yesterday at 3:11 PM", "Mon 09:12", "1/4/26, 9:02 PM".
_TIME = re.compile(
    r"^(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?$|^yesterday at .+$|^\d{1,2}/\d{1,2}/\d{2,4},? .*$",
    re.I,
)

# "Alice — 12:04" / "Alice 12:04". Speakers are often on their own line in
# Discord and Slack; WhatsApp puts the name above the bubble. Either way the
# name is short and does not end in a full stop.
_NAME_WITH_TIME = re.compile(r"^(?P<name>[^:]{1,48}?)\s*[,–—-]?\s*(?P<time>\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)$", re.I)

_OWN_LABELS = {"you", "me", "yourself", "self"}
_OWN_PREFIX = re.compile(r"^(?:you|me)\s*:\s*", re.I)

# Emoji-only lines in a reaction row are not messages.
_ONLY_EMOJI = re.compile(r"^[\W_]+$", re.UNICODE)

# A quote-reply block starts with a "> " or the app's reply marker.
_QUOTE = re.compile(r"^(?:>|\|\||↳)\s?")

_CONTINUATION = re.compile(r"^[a-z0-9,，、)\]}'\"…%]")


@dataclass
class Message:
    sender: str
    text: str
    mine: bool = False
    index: int = 0

    def __post_init__(self) -> None:
        self.text = self.text.strip()

    @property
    def is_empty(self) -> bool:
        return not self.text

    def as_dict(self) -> dict[str, Any]:
        return {"sender": self.sender, "text": self.text, "mine": self.mine}


@dataclass
class ScreenRead:
    """One look at the screen, normalised."""

    app: str = "generic"
    thread: str = ""
    participants: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    # What the person has typed but not sent, if the app exposes it.
    draft: str = ""
    # Set by the caller when the thread has visibly been read but not answered.
    other_read: bool = False
    self_names: list[str] = field(default_factory=list)
    dropped: int = 0  # lines that looked like chrome, for the debug view

    @property
    def profile(self) -> AppProfile:
        return profile(self.app)

    # --- derived facts the reading and the guard both use -------------------

    @property
    def last(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    @property
    def last_theirs(self) -> Message | None:
        for m in reversed(self.messages):
            if not m.mine:
                return m
        return None

    @property
    def trailing_mine(self) -> int:
        """How many messages in a row I have sent into silence."""
        n = 0
        for m in reversed(self.messages):
            if m.mine:
                n += 1
            else:
                break
        return n

    @property
    def waiting_on_me(self) -> bool:
        last = self.last
        return bool(last and not last.mine)

    def others(self) -> list[str]:
        seen: list[str] = []
        for m in self.messages:
            if not m.mine and m.sender and m.sender not in seen:
                seen.append(m.sender)
        return seen

    def median_length_theirs(self) -> int:
        lens = sorted(len(m.text) for m in self.messages if not m.mine and m.text)
        if not lens:
            return 80
        return lens[len(lens) // 2]

    def as_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "thread": self.thread,
            "participants": self.participants or self.others(),
            "messages": [m.as_dict() for m in self.messages],
            "draft": self.draft,
            "trailing_mine": self.trailing_mine,
            "waiting_on_me": self.waiting_on_me,
        }


# ---------------------------------------------------------------- parsing


def _is_chrome(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    low = t.lower().strip(" .:•·-–")
    if low in _CHROME_EXACT:
        return True
    if _TIME.match(t):
        return True
    # Decorative separators, but a lone "?" or "!" is a real follow-up.
    if len(t) == 1 and not t.isalnum() and t not in "?!¿¡+":
        return True
    # "3 unread messages", "Alice is typing…"
    if re.fullmatch(r"\d+\s+(?:new\s+)?(?:unread\s+)?messages?", low):
        return True
    if low.endswith(("is typing…", "is typing...", "is typing")):
        return True
    # Compose boxes: "message #general", "Message @Alice", "Send a message".
    if re.fullmatch(r"(?:send a |write a )?message(?: to)?(?: #\S+| @\S+)?", low):
        return True
    return False


def _speaker_from_name_line(text: str) -> tuple[str, str] | None:
    """A line that is only a speaker name, as Discord and Slack print."""
    t = text.strip().rstrip(":")
    if not (1 <= len(t) <= 40):
        return None
    if any(c in t for c in ".!?,") or " " in t.strip() and len(t.split()) > 3:
        return None
    if t[0].isupper() and t.replace(" ", "").replace("_", "").replace("-", "").replace(".", "").isalnum():
        return t, ""
    return None


def _strip_prefix_speaker(text: str) -> tuple[str | None, str]:
    """'Alice: hello' or 'Alice  12:04' -> ('Alice', 'hello')."""
    m = _OWN_PREFIX.match(text)
    if m:
        return "me", text[m.end():].strip()
    m = _NAME_WITH_TIME.match(text)
    if m and m.group("name").strip():
        return m.group("name").strip(), ""
    if ":" in text:
        head, _, tail = text.partition(":")
        head = head.strip()
        if 1 <= len(head) <= 40 and len(head.split()) <= 4 and head[0].isalpha() and not _TIME.match(head):
            return head, tail.strip()
    return None, text


def _looks_continuation(prev: str, text: str) -> bool:
    if not prev:
        return False
    if prev.endswith((".", "!", "?", "…", ":", ";", '"')):
        return False
    return bool(_CONTINUATION.match(text))


def parse(
    raw: dict[str, Any] | list[Any] | str,
    *,
    app: str | None = None,
    self_names: Iterable[str] = (),
    draft: str | None = None,
    max_turns: int = 40,
    max_chars: int = 6000,
    keep_senders: Iterable[str] = (),
) -> ScreenRead:
    """Turn a raw screen read into a conversation.

    Accepts the shape the Android app sends::

        {"app": "discord", "thread": "#general",
         "nodes": [{"text": "Alice", "bounds": [0,0,400,80]},
                   {"text": "hey are you around", "bounds": [0,80,900,140]}],
         "draft": "yeah whats up", "self_names": ["You"],
         "width": 1080}

    and a plain list of strings or one newline-separated string, which is what a
    copied screen or a test fixture looks like.
    """
    if isinstance(raw, str):
        raw = {"nodes": [{"text": line} for line in raw.splitlines()]}
    elif isinstance(raw, list):
        raw = {"nodes": [{"text": x} if isinstance(x, str) else x for x in raw]}
    elif not isinstance(raw, dict):
        raise TypeError("screen read must be an object, a list of lines, or a string")

    app_id = (app or raw.get("app") or raw.get("package") or "generic")
    prof = profile(app_id)
    if prof.id == "generic" and raw.get("app"):
        app_id = str(raw["app"])
    read = ScreenRead(
        app=app_id,
        thread=str(raw.get("thread") or raw.get("channel") or ""),
        participants=[str(p) for p in raw.get("participants") or []],
        self_names=[str(s) for s in (raw.get("self_names") or self_names)],
        draft=str(draft if draft is not None else raw.get("draft") or ""),
        other_read=bool(raw.get("other_read")),
    )

    width = float(raw.get("width") or 0)
    nodes = raw.get("nodes") or raw.get("lines") or raw.get("items") or []
    mine_names = {n.strip().lower() for n in read.self_names} | _OWN_LABELS
    header = read.thread.strip().lower()

    # sender, text, mine, quote, x, top, bottom, has_bounds
    rows: list[tuple[str, str, bool, bool, int, float, float, bool]] = []
    pending: str | None = None   # a name line waiting for its message
    last_sender = ""             # who spoke most recently
    seen_bubble = False          # has anything real been read yet

    for node in nodes:
        if isinstance(node, str):
            node = {"text": node}
        raw_text = str(node.get("text") or node.get("label") or node.get("content") or "").strip()
        if not raw_text:
            continue
        bounds = node.get("bounds") or node.get("rect") or []
        x = float(bounds[0]) if len(bounds) >= 1 and isinstance(bounds[0], (int, float)) else -1
        # Right-hand bubbles are mine in every chat app that right-aligns. The
        # reliable signal is which screen edge the bubble hugs, not where it
        # starts: a long message of mine can begin at 25% of the width.
        on_right = False
        if width and len(bounds) >= 4:
            left, right = float(bounds[0]), float(bounds[2])
            on_right = (width - right) < left
        elif width and len(bounds) >= 3:
            on_right = x >= width * 0.40

        has_bounds = len(bounds) >= 4
        top = float(bounds[1]) if has_bounds else 0.0
        bottom = float(bounds[3]) if has_bounds else 0.0

        quote = bool(_QUOTE.match(raw_text))
        text = _QUOTE.sub("", raw_text).strip()

        # 1. App furniture: the channel header, timestamps, "is typing".
        if not on_right and _is_chrome(text):
            read.dropped += 1
            continue
        if not quote and text.lower().strip("# ") == header.strip("# "):
            read.dropped += 1
            continue
        if node.get("editable") or node.get("role") in ("textbox", "input"):
            read.draft = read.draft or text  # the compose box, not a message
            continue

        explicit = node.get("speaker") or node.get("sender") or node.get("author")
        forced_mine = node.get("mine") is True
        sender: str | None = str(explicit).strip() if explicit else None
        body = text

        # 2. "Alice: hello" / "You  9:41 AM" / "Alice  9:41 AM"
        if sender is None and not on_right and not forced_mine:
            got, rest = _strip_prefix_speaker(raw_text)
            if got is not None:
                if got.lower() in mine_names:
                    sender, forced_mine = "You", True
                else:
                    sender = got
                body = rest.strip()
                if not body:
                    # a bare "Alice" or "Alice 12:04" header line
                    pending, last_sender = sender, sender
                    continue

        # 3. A line that is only a name, as Discord and Slack print it.
        if sender is None and not on_right and not forced_mine:
            if raw_text.strip().lower() in mine_names and len(raw_text.split()) == 1:
                pending, last_sender = "You", "You"
                continue
            named = _speaker_from_name_line(raw_text)
            if named:
                pending, last_sender = named[0], named[0]
                continue

        # 4. Whoever is named, or whoever spoke last.
        if sender is None:
            if on_right or forced_mine:
                sender = "You"
            elif pending:
                sender = pending
            else:
                sender = last_sender
        mine = bool(forced_mine or on_right or sender.strip().lower() in mine_names)
        if mine:
            sender = "You"
        if not mine and sender.lower() not in mine_names:
            last_sender = sender
        pending = None
        seen_bubble = True
        if not body:
            continue
        rows.append((sender, body, mine, quote, int(x), top, bottom, has_bounds))

    read.messages = _assemble(rows, mine_names)
    read.messages = _trim(read.messages, max_turns, max_chars, keep_senders)
    for i, m in enumerate(read.messages):
        m.index = i
    if not read.participants:
        read.participants = read.others()
    return read


# Two nodes are the same bubble when they are on the same side, from the same
# person, and the second one starts where the first left off. The gap threshold
# is in screen pixels: a wrapped line has no gap, two bubbles always do.
_SAME_BUBBLE_GAP = 6.0
_SAME_BUBBLE_X = 6.0


def _assemble(rows: list[tuple], mine_names: set[str] | None = None) -> list[Message]:
    """Merge lines that belong to one bubble into one message."""
    out: list[Message] = []
    prev: tuple | None = None
    for row in rows:
        label, text, mine, quote, x, top, bottom, has_bounds = row
        last = out[-1] if out else None
        mergeable = False
        if last is not None and prev is not None and not quote:
            same_side = last.mine == mine
            same_sender = last.sender.lower() == label.lower()
            if has_bounds != prev[7]:
                # One has geometry and one does not, so they came from different
                # parts of the screen (a dialog above the chat, a header, a
                # label). Merging across that boundary is how UI text ends up
                # inside a message.
                mergeable = False
            elif has_bounds and prev[7]:
                # Both have geometry: trust it over the text.
                gap = top - prev[6]
                aligned = abs(x - prev[4]) <= _SAME_BUBBLE_X
                touching = -2.0 <= gap <= _SAME_BUBBLE_GAP
                mergeable = same_side and same_sender and aligned and touching
            else:
                # Pure text dump with no geometry: fall back to how the line reads.
                mergeable = (same_side and (same_sender or not label or not last.sender)
                             and _looks_continuation(last.text, text))
        if mergeable:
            last.text = f"{last.text}\n{text}".strip()
            if label and not last.sender:
                last.sender = label
        else:
            out.append(Message(sender=label, text=text, mine=mine))
        prev = row
    return out


def _trim(messages: list[Message], max_turns: int, max_chars: int, keep: Iterable[str]) -> list[Message]:
    """Keep the most recent turns, plus any sender the user asked to always keep."""
    keep_low = {k.strip().lower() for k in keep if k}
    kept = messages[-max_turns:] if max_turns > 0 else list(messages)
    if keep_low:
        earlier = messages[: max(0, len(messages) - max_turns)]
        pinned = [m for m in earlier if m.sender.lower() in keep_low][-6:]
        if pinned:
            kept = pinned + kept
            # de-duplicate if the pin and the window overlap
            seen: set[int] = set()
            uniq: list[Message] = []
            for m in kept:
                if id(m) in seen:
                    continue
                seen.add(id(m))
                uniq.append(m)
            kept = uniq
    if max_chars > 0:
        total = 0
        out: list[Message] = []
        for m in reversed(kept):
            total += len(m.text)
            if total > max_chars and out:
                break
            out.append(m)
        kept = list(reversed(out))
    return kept


def render(read: ScreenRead, *, max_chars: int = 6000) -> str:
    """The transcript as the model sees it. Name, then line, mine marked."""
    lines: list[str] = []
    for m in read.messages:
        who = "You" if m.mine else (m.sender or "Them")
        body = m.text.replace("\n", " / ")
        lines.append(f"{who}: {body}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = "…[earlier messages trimmed]…\n" + out[-max_chars:]
    return out
