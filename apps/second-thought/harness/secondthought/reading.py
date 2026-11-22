"""Reading the conversation and offering what to say.

This is the half the person asked for: look at the whole conversation, work out
what is actually going on and what they are trying to do, and put three or four
things in front of them that they could send.

It is deliberately not "generate a reply". Three things happen before an option
is shown:

1. **A reading.** Who is talking, what the last message wants, what the person
   wants, what is at stake, what is unresolved, and what is *not* in the
   transcript that matters. Shown collapsed at the top of the overlay, because
   when it is wrong that is the useful thing to see.
2. **Options, with a move.** Every option names what it does - answer, deflect,
   buy time, set a boundary, be warm, close it off, ask back - and who it is for.
   Four near-identical rephrasings are worse than one good line, so the model is
   told to give distinct moves.
3. **A local filter.** Options are dropped or demoted offline when they are too
   long for the thread, repeat something already suggested in this session, are
   written in a register the thread does not use, or do not explain themselves.
   The model's own risk score is folded in, and the top option is the one that is
   least likely to be regretted, not the one that is funniest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .guard import rule_findings, _combine
from .model import Client, ModelError
from .transcript import ScreenRead, render

READING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "what_they_want": {"type": "string"},
                "what_you_want": {"type": "string"},
                "stakes": {"type": "string", "enum": ["low", "medium", "high"]},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "noticed": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["situation", "what_they_want", "what_you_want", "stakes"],
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "move": {
                        "type": "string",
                        "enum": ["answer", "ask_back", "warm", "deflect", "buy_time",
                                 "boundary", "close_out", "repair", "decline", "joke"],
                    },
                    "tone": {"type": "string"},
                    "why": {"type": "string"},
                    "risk": {"type": "integer"},
                },
                "required": ["text", "move", "tone", "why", "risk"],
            },
        },
        "suggested_intents": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "options"],
}

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")

MOVES = {
    "answer": "answers what was asked",
    "ask_back": "asks one clear question back",
    "warm": "leads with the relationship",
    "deflect": "moves off the topic without lying",
    "buy_time": "buys time and says when",
    "boundary": "says no or not now, plainly",
    "close_out": "ends the thread cleanly",
    "repair": "acknowledges something that went wrong",
    "decline": "says no to the thing itself",
    "joke": "releases the tension",
}

READING_SYSTEM = """You are the reading half of a phone overlay that sits over \
someone's messaging apps while they read. You see one conversation, as a \
screen scrape, and you help its owner say what they mean - better and faster \
than they would alone.

What you do, in order:

1. Read the whole conversation. Not the last message alone. Who is speaking, \
who is waiting, what has already been said so it is not said twice, what the \
tone of this particular thread is (a group of friends is not a workplace), and \
what the last message actually wants - which is often not what it says.
2. Work out the owner's intention. They will usually have told you something in \
their standing intentions. Combine that with what the thread needs.
3. Offer 3 or 4 things they could send now.

Rules for the options:
- Each option must do a different thing. Name the move. Never give four \
rephrasings of the same sentence.
- Every option must be sendable as-is. No brackets, no [your name], no \
placeholders, no "insert X".
- Match the thread: its length, its casing, its punctuation, its formality, \
whether it uses emoji. A 30-word reply to a one-word message is a mistake even \
when the words are good.
- Write in the owner's voice, not yours. If their voice notes say lowercase, \
write lowercase. Do not add a personality they do not have.
- Never invent facts, plans, times, feelings or shared history that are not in \
the transcript or the owner's intentions. If the reply needs a fact nobody has \
supplied, offer the version that asks for it instead of guessing.
- If the honest answer is that nothing should be sent, say so in 'situation' \
and make the options small: an acknowledgement, or an offer to reply later.
- 'why' is one plain sentence, under 18 words, explaining what the option does \
for them. Not a restatement of the option.
- 'risk' 0-100 is how likely this option is to go wrong, judged the same way a \
careful friend would. Do not set it low out of politeness.
- 'suggested_intents' is up to three short intentions the owner might want to \
add next time ("just say yes to dinner", "delay without a date"), written in \
their voice.

If the owner has typed something already, the draft is included. Work with it \
if it can be saved; do not discard it silently. If they have asked for \
something specific in 'they want to say', that is the thing to serve.

Reply with JSON only."""


@dataclass
class Option:
    text: str
    move: str = "answer"
    tone: str = ""
    why: str = ""
    risk: int = 0
    notes: list[str] = field(default_factory=list)
    # local penalties applied on top of the model's risk
    adjusted: int = 0

    @property
    def move_label(self) -> str:
        return MOVES.get(self.move, self.move)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "move": self.move,
            "move_label": self.move_label,
            "tone": self.tone,
            "why": self.why,
            "risk": self.adjusted or self.risk,
            "model_risk": self.risk,
            "notes": self.notes,
        }


@dataclass
class Reading:
    situation: str = ""
    what_they_want: str = ""
    what_you_want: str = ""
    stakes: str = "low"
    open_questions: list[str] = field(default_factory=list)
    noticed: list[str] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    suggested_intents: list[str] = field(default_factory=list)
    backend: str = "fixture"
    ms: int = 0
    error: str = ""
    app: str = ""
    thread: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "reading": {
                "situation": self.situation,
                "what_they_want": self.what_they_want,
                "what_you_want": self.what_you_want,
                "stakes": self.stakes,
                "open_questions": self.open_questions,
                "noticed": self.noticed,
            },
            "options": [o.as_dict() for o in self.options],
            "suggested_intents": self.suggested_intents,
            "backend": self.backend,
            "ms": self.ms,
            "error": self.error,
        }


def _prompt(read: ScreenRead, cfg: Config, steer: str, draft: str) -> str:
    prof = read.profile
    people = "\n".join(
        f"- {name}: {note}" for name, note in cfg.intent.people.items()
        if name.lower() in " ".join(read.others()).lower() or name.lower() in read.thread.lower()
    )
    goals = "\n".join(f"- {g}" for g in cfg.intent.goals) or "- (none stated)"
    avoid = "\n".join(f"- {a}" for a in cfg.intent.avoid) or "- (none stated)"
    wait = ("They sent the last message. A reply is expected." if read.waiting_on_me
            else f"You sent the last {read.trailing_mine} message(s) and there has been no reply.")
    used_emoji = "yes" if _EMOJI.search(" ".join(m.text for m in read.messages)) else "no"
    facts = [
        f"- app: {prof.label} ({prof.register})",
        f"- norms: {prof.conventions}",
        f"- thread: {read.thread or 'a direct conversation'}",
        f"- with: {', '.join(read.others()) or 'someone'}",
        f"- state: {wait}",
        f"- their messages run about {read.median_length_theirs()} characters; yours should be near that",
        f"- thread has used emoji: {used_emoji}",
    ]
    steer_block = f"\nWHAT THEY WANT TO SAY RIGHT NOW (serve this):\n{steer}\n" if steer.strip() else ""
    draft_block = f"\nDRAFT THEY HAVE TYPED (work with it if it is worth saving):\n{draft}\n" if draft.strip() else ""
    return f"""CONVERSATION ({len(read.messages)} visible messages):
{render(read, max_chars=cfg.watch.max_chars) or "(nothing readable on screen)"}

FACTS ABOUT THE ROOM:
{chr(10).join(facts)}
{people}

THEIR STANDING INTENTIONS:
{goals}

THEIR VOICE:
{cfg.intent.voice}

THINGS THEY ASKED TO AVOID:
{avoid}
{steer_block}{draft_block}
Give the reading and the options. Reply with the JSON object."""


def read_conversation(client: Client, read: ScreenRead, cfg: Config, *, steer: str = "",
                      draft: str = "", exclude: list[str] | None = None) -> Reading:
    if not read.messages and not draft:
        return Reading(
            situation="Nothing readable on this screen.",
            what_they_want="", what_you_want="",
            backend="local", error="empty transcript",
            app=read.app, thread=read.thread,
        )
    messages = [
        {"role": "system", "content": READING_SYSTEM},
        {"role": "user", "content": _prompt(read, cfg, steer, draft)},
    ]
    out = Reading(app=read.app, thread=read.thread)
    try:
        data, comp = client.structured(messages, READING_SCHEMA, max_tokens=cfg.model.max_tokens)
        out.backend, out.ms = comp.backend, comp.ms
    except ModelError as e:
        out.error = str(e)
        return _rules_only(read, cfg, out)

    intent = data.get("intent") or {}
    out.situation = str(intent.get("situation") or "").strip()
    out.what_they_want = str(intent.get("what_they_want") or "").strip()
    out.what_you_want = str(intent.get("what_you_want") or "").strip()
    stakes = str(intent.get("stakes") or "low").lower()
    out.stakes = stakes if stakes in ("low", "medium", "high") else "low"
    out.open_questions = [str(x) for x in (intent.get("open_questions") or [])][:5]
    out.noticed = [str(x) for x in (intent.get("noticed") or [])][:5]
    out.suggested_intents = [str(x) for x in (data.get("suggested_intents") or [])][:3]

    for raw in (data.get("options") or [])[:6]:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        out.options.append(Option(
            text=text,
            move=str(raw.get("move") or "answer"),
            tone=str(raw.get("tone") or ""),
            why=str(raw.get("why") or "").strip(),
            risk=_int(raw.get("risk"), 30),
        ))
    out.options = rank(out.options, read, cfg, exclude or [])
    if not out.options:
        return _rules_only(read, cfg, out)
    return out


def _int(v: Any, default: int) -> int:
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return default


_PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}|<[^>]{1,40}>|\b(your name|insert .{1,20})\b", re.I)


def rank(options: list[Option], read: ScreenRead, cfg: Config, exclude: list[str]) -> list[Option]:
    """Offline scoring: adjust the model's risk with what we can check ourselves.

    Nothing is dropped for a small fault. A placeholder, a duplicate of what was
    already offered, or a paragraph in a one-liner thread is *demoted* and the
    reason is shown, because a hidden option teaches the person nothing. An
    option is only removed when it is not sendable at all (a placeholder) or so
    risky it would be dishonest to put it first.
    """
    median = read.median_length_theirs()
    thread_emoji = bool(_EMOJI.search(" ".join(m.text for m in read.messages)))
    draft_norm = [d.strip().lower() for d in exclude]
    scored: list[tuple[int, Option]] = []
    seen_heads: set[str] = set()
    seen_texts: set[str] = set()
    for o in options:
        penalty = 0
        notes = list(o.notes)
        unsendable = False
        if o.text.strip().lower() in seen_texts:
            unsendable = True
            notes.append("says exactly the same thing as another option")
        seen_texts.add(o.text.strip().lower())
        if _PLACEHOLDER.search(o.text):
            unsendable = True
            notes.append("has a placeholder in it, so you cannot send it as-is")
        n = len(o.text)
        if median and n > max(240, median * 7):
            penalty += 25
            notes.append("much longer than the thread")
        if median <= 30 and len(o.text.split()) > 25 and read.profile.id in ("sms", "instagram", "discord"):
            penalty += 15
            notes.append("a paragraph in a one-liner thread")
        if o.text.strip().lower() in draft_norm:
            penalty += 45
            notes.append("you were already shown this one")
        if _EMOJI.search(o.text) and not thread_emoji:
            penalty += 12
            notes.append("emoji where the thread has none")
        if not o.why:
            penalty += 10
            notes.append("does not say what it is for")
        if "?" in o.text and not read.waiting_on_me and read.trailing_mine >= 2:
            penalty += 20
            notes.append("another question into a thread that has gone quiet")
        head = " ".join(o.text.lower().split()[:3])
        if head in seen_heads:
            penalty += 30
            notes.append("says almost the same thing as another option")
        seen_heads.add(head)
        o.notes = notes
        o.adjusted = max(0, min(100, o.risk + penalty))
        if unsendable:
            continue
        scored.append((o.adjusted, o))

    # Lowest risk first; among equals, a real answer before a flourish.
    scored.sort(key=lambda pair: (pair[0], 0 if pair[1].move == "answer" else 1, len(pair[1].text)))
    out = [o for risk, o in scored if risk < 95][:4]
    if not out:  # everything scored badly; show the least bad with its warning
        out = [o for _risk, o in scored[:1]]
    return out


def _rules_only(read: ScreenRead, cfg: Config, out: Reading) -> Reading:
    """When there is no model, still be useful: honest facts and a safe line."""
    out.backend = out.backend or "local"
    findings = rule_findings(read, "", cfg)
    theirs = read.last_theirs
    if not out.situation:
        if read.waiting_on_me and theirs:
            out.situation = f"{theirs.sender or 'They'} sent the last message. It has not been answered."
            out.what_they_want = "A reply, or at least an acknowledgement."
        elif read.trailing_mine:
            out.situation = f"You are {read.trailing_mine} message(s) in with no answer."
            out.what_you_want = "Either a real answer or to stop waiting by the phone."
            out.what_they_want = "Nothing yet. Silence is currently theirs."
        else:
            out.situation = "Nothing in this thread is waiting on you."
    out.findings_score = _combine(findings)  # type: ignore[attr-defined]
    if theirs and read.waiting_on_me:
        out.options = rank([
            Option(
                text="sorry, just seen this - what do you need?",
                move="answer",
                tone="neutral",
                why="Acknowledges the message without pretending you have an answer yet.",
                risk=12,
            ),
            Option(
                text="give me a bit and i'll come back to you properly",
                move="buy_time",
                tone="honest",
                why="Says when, so the silence stops being ambiguous.",
                risk=8,
            ),
        ], read, cfg, [])
    elif read.trailing_mine >= 2:
        out.options = rank([
            Option(
                text="no rush, whenever you get a sec",
                move="close_out",
                tone="easy",
                why="Takes the pressure out of the last two messages without pretending they were not sent.",
                risk=10,
            ),
        ], read, cfg, [])
        out.what_you_want = "To stop the thread from feeling like a countdown."
    return out
