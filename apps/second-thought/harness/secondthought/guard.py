"""The "don't let me say something stupid" half.

Two layers, and the cheap one runs first:

**Rules** (offline, always on, no model). A small set of checks that catch the
mistakes the model is worst at noticing because they need *context the model
does not have from the draft alone*: this is the fourth message into silence,
this person was already apologised to, the thread has been one-liners and this
is 900 characters, the promise is on a night when you famously say yes to
everything.

**Judgement** (the model). Reads the conversation, the person's voice, their
stated intentions, and the draft, and answers one question: would sending this
read as a mistake, and why. It is told to be quiet unless something is actually
wrong. Silence is the default and the most common correct answer.

The nudge never blocks. It shows what it noticed and offers a rewrite that the
person can copy. The overlay never presses send and never edits the field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .model import Client, ModelError, extract_json
from .transcript import ScreenRead, render

# Scores are "how sorry will you be if this lands badly", 0-100.
# 0-34 say nothing, 35-64 a quiet note, 65+ a firmer note and (in hold mode) a
# second tap. The thresholds live in GuardConfig.min_risk.

GUARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk": {"type": "integer"},
        "why": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "fix": {"type": "string"},
        "alt": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["fine", "too_much", "too_cold", "too_soon", "promise",
                     "misread", "rambling", "repetitive", "wrong_audience", "vague"],
        },
    },
    "required": ["risk", "why", "reasons", "category"],
}


@dataclass
class Finding:
    """One reason the draft is risky, from a rule or from the model."""

    rule: str
    score: int
    detail: str
    level: str = "note"  # "note" | "stop"

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "score": self.score, "detail": self.detail, "level": self.level}


@dataclass
class Verdict:
    risk: int = 0
    reasons: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    why: str = ""
    fix: str = ""
    alt: str = ""
    category: str = "fine"
    backend: str = "rules"
    ms: int = 0
    error: str = ""

    @property
    def worth_showing(self) -> bool:
        return self.risk >= 1 and bool(self.reasons or self.why)

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "reasons": self.reasons,
            "findings": [f.as_dict() for f in self.findings],
            "why": self.why,
            "fix": self.fix,
            "alt": self.alt,
            "category": self.category,
            "backend": self.backend,
            "ms": self.ms,
            "error": self.error,
        }


# ---------------------------------------------------------------- rules

_PROMISE = re.compile(
    r"\b(i promise|i swear|consider it done|i guarantee|count me in|"
    r"i'll handle|i'll sort|i'll cover|i can make that|i'll do it all|"
    r"anything you need|whatever you need|on my way)\b", re.I
)
_ABSOLUTE = re.compile(r"\b(always|never|everyone|nobody|no one|literally|obviously|you always|you never)\b", re.I)
_ANGER = re.compile(r"\b(whatever|unbelievable|are you serious|seriously\?|fuck|fucking|shut up|"
                    r"i'm done|forget it|never mind|whatever\.)\b", re.I)
_APOLOGY = re.compile(r"\b(sorry|apolog|my bad|i apologize)\b", re.I)
_ULTIMATUM = re.compile(r"\b(or else|last chance|final warning|don't bother|i'm out|we're done|"
                        r"if you don't|either .{1,40} or)\b", re.I)
_VAGUE = re.compile(r"^(ok|okay|k|yeah|yep|lol|haha|sure|cool|nice|same)\W*$", re.I)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _sentences(text: str) -> int:
    return max(1, len([s for s in re.split(r"[.!?…\n]+", text) if s.strip()]))


def rule_findings(read: ScreenRead, draft: str, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    d = draft.strip()
    if not d:
        return out
    theirs = read.last_theirs
    their_text = theirs.text if theirs else ""
    median = read.median_length_theirs()

    # 1. Speaking into silence, again.
    trailing = read.trailing_mine
    weight = read.profile.followup_weight
    if trailing >= 2:
        score = {"high": 70, "normal": 52, "low": 28}[weight]
        out.append(Finding(
            "double_text",
            score,
            f"This would be message {trailing + 1} in a row with no reply from {theirs.sender or 'them'}. "
            "On a phone, the number of unanswered messages is visible and it reads as pressure.",
            "stop" if score >= 65 else "note",
        ))
    elif trailing == 1 and weight == "high":
        out.append(Finding("follow_up", 30,
                           f"You already sent the last word. A second one before they answer usually reads "
                           f"as anxiety rather than urgency.", "note"))

    # 2. Apologising again for something already apologised for.
    if _APOLOGY.search(d) and _APOLOGY.search(" ".join(m.text for m in read.messages if m.mine)):
        out.append(Finding("repeat_apology", 45,
                           "You have apologised to this person already in this thread. A second apology "
                           "reopens it instead of closing it.", "note"))

    # 3. Length against the room.
    if median and len(d) > max(220, median * 6) and len(d.split()) > 35:
        out.append(Finding("wall_of_text", 48,
                           f"Your draft is {len(d)} characters; their messages have been running about "
                           f"{median}. In this thread that reads as either a speech or a fight.", "note"))
    elif median <= 24 and len(d.split()) > 12 and read.profile.id in ("sms", "instagram", "discord"):
        out.append(Finding("tone_change", 34,
                           "The thread has been one-liners and this is a paragraph. It will look like "
                           "something happened.", "note"))

    # 4. Committing on someone else's behalf.
    if _PROMISE.search(d) and _sentences(d) <= 3:
        out.append(Finding("promise", 55,
                           "This is a commitment. It is a firm yes to a time, an amount or a favour, in a "
                           "message you cannot un-send. If you have not decided, say so in words.", "stop"))

    # 5. Anger or an ultimatum.
    if _ANGER.search(d) or _ULTIMATUM.search(d):
        out.append(Finding("heat", 68,
                           "There is heat in this. Whatever you send now is the version they keep, and a "
                           "screenshot is one tap away.", "stop"))

    # 6. Absolutes that will be quoted back.
    if _ABSOLUTE.search(d) and len(d.split()) > 6:
        out.append(Finding("absolute", 32,
                           "Words like always and never are easy to disprove and hard to take back. Say the "
                           "specific thing instead.", "note"))

    # 7. A bare 'ok' when they asked a real question.
    if _VAGUE.match(d) and their_text.rstrip().endswith("?") and len(their_text.split()) > 3:
        out.append(Finding("brush_off", 40,
                           f"{theirs.sender or 'They'} asked a real question. A one-word answer will read as "
                           "a door closing, whether or not you meant it.", "note"))

    # 8. Emoji where the room has none.
    thread_text = " ".join(m.text for m in read.messages)
    if _EMOJI.search(d) and not _EMOJI.search(thread_text) and read.profile.id in ("linkedin", "slack", "sms"):
        out.append(Finding("wrong_register", 36,
                           "There are no emoji anywhere in this thread. One from you will stand out on the "
                           "one message everyone rereads.", "note"))

    # 9. Their own stated avoid-list, plus per-person notes, cheaply.
    low = d.lower()
    for item in cfg.intent.avoid:
        key = item.split("(")[0].strip().lower()
        if key and len(key) > 8 and key in low:
            out.append(Finding("stated_avoid", 50, f"You asked to avoid this: {item}", "note"))

    # 10. Reply to a message that moved on.
    if read.waiting_on_me and theirs and len(read.messages) > 1:
        after = read.messages[-1]
        if after.index > theirs.index:
            out.append(Finding("stale_reply", 30,
                               f"You look like you are answering {theirs.sender}'s older message, but the "
                               "thread has moved since.", "note"))
    return out


def _combine(findings: list[Finding]) -> int:
    """Two small worries are worth more than either alone, but not additive."""
    if not findings:
        return 0
    top = max(f.score for f in findings)
    rest = sorted((f.score for f in findings if f.score != top), reverse=True)[:3]
    bump = sum(int(s * 0.28) for s in rest)
    return max(0, min(100, top + bump))


# ---------------------------------------------------------------- model call

GUARD_SYSTEM = """You are the part of a phone overlay that stops its owner from \
sending something they will regret. You are not a chatbot and you never write \
the message for them unless asked.

You will be given: who they are talking to and in which app, the recent \
conversation, a draft they are about to send, their standing intentions, and \
things they have asked to avoid.

Judge the draft. Most drafts are fine: when the draft is fine you must say so \
with a low risk. Only raise the score when you can name the specific thing that \
would go wrong, in the terms above. Do not moralise, do not comment on \
grammar, do not object to swearing that the thread already uses, and do not \
invent context that is not in the transcript.

Score 0-100 by how much they will wish they had not sent it:
0-20  fine. Ordinary message, nothing at risk.
21-44 slightly off: blunt, a bit much, a bit cold. Worth a quiet word.
45-69 likely regret: pressure, a promise they have not decided, heat, \
misreading who is listening.
70-100 do not send this: it will change how this person sees them.

Write 'why' as one plain sentence in the second person, under 20 words, no \
preamble. 'reasons' are up to three short fragments naming what you saw. \
'fix' is the smallest edit that keeps their meaning, not a rewrite into your \
voice. 'alt' is a whole replacement message in THEIR voice, only if the draft \
is beyond a small edit; otherwise leave it empty. Leave 'fix' and 'alt' empty \
when the risk is under 45.

Their voice: use it for 'alt'. Their intentions: they are instructions to you. \
Things to avoid: hard constraints.

Reply with JSON only."""


def _context(read: ScreenRead, draft: str, cfg: Config) -> str:
    prof = read.profile
    who = ", ".join(read.others()) or "someone"
    people_notes = "\n".join(
        f"- {name}: {note}" for name, note in cfg.intent.people.items()
        if name.lower() in who.lower() or name.lower() in read.thread.lower()
    )
    goals = "\n".join(f"- {g}" for g in cfg.intent.goals) or "- (none stated)"
    avoid = "\n".join(f"- {a}" for a in cfg.intent.avoid) or "- (none stated)"
    notes = read.thread or "a direct conversation"
    return f"""APP: {prof.label} ({prof.id}). Your reading of the room: {prof.register}.
APP NORMS: {prof.conventions}

CONVERSATION SO FAR (they are the other people, 'You' is the draft writer):
{render(read, max_chars=cfg.watch.max_chars) or "(no messages visible)"}

STATE: {'They sent the last message; a reply is expected.' if read.waiting_on_me else 'You sent the last message and there has been no reply.'}
You have sent {read.trailing_mine} message(s) in a row without an answer.

DRAFT ABOUT TO BE SENT:
{draft}

WHO: {who}. Thread: {notes}.
{people_notes}
THEIR INTENTIONS RIGHT NOW:
{goals}
THINGS THEY ASKED TO AVOID:
{avoid}
THEIR VOICE:
{cfg.intent.voice}
"""


def review(client: Client, read: ScreenRead, draft: str, cfg: Config) -> Verdict:
    """Score the draft. Rules first, model second, and the model hears the rules."""
    findings = rule_findings(read, draft, cfg)
    rule_score = _combine(findings)
    verdict = Verdict(
        risk=rule_score,
        findings=findings,
        reasons=[f.detail for f in findings],
        backend="rules",
        category="fine" if not findings else findings[0].rule,
    )
    if not cfg.guard.enabled or cfg.guard.strength == "off":
        return verdict
    if len(draft.strip()) < cfg.guard.min_chars:
        verdict.risk = min(verdict.risk, 20)
        return verdict

    rules_block = "\n".join(f"- {f.rule}: {f.detail}" for f in findings) or "- (nothing from the rules)"
    messages = [
        {"role": "system", "content": GUARD_SYSTEM},
        {"role": "user", "content": _context(read, draft, cfg)
            + f"\nTHE RULES ALREADY FLAGGED:\n{rules_block}\n\n"
              "Do not repeat a rule finding as if you found it yourself unless you can add something. "
              "If the rules are wrong for this thread, say the draft is fine. Reply with the JSON object."},
    ]
    try:
        data, comp = client.structured(messages, GUARD_SCHEMA, max_tokens=420, temperature=0.2)
    except ModelError as e:
        verdict.error = str(e)
        return verdict

    model_risk = _int(data.get("risk"), 0)
    # The model hears the rules, so it often restates one. A reason said twice
    # is noise in a small panel, so keep the rule's phrasing and drop the echo.
    seen = [_norm(f.detail) for f in findings]
    model_findings = []
    for r in (data.get("reasons") or []):
        text = str(r).strip()
        if not text:
            continue
        n = _norm(text)
        if any(n in other or other in n for other in seen if other):
            continue
        seen.append(n)
        model_findings.append(
            Finding("model", model_risk, text[:300], "stop" if model_risk >= 65 else "note")
        )
    verdict.findings = findings + model_findings
    # The model sees the rules, so it usually lands at or above them. Trust the
    # higher of the two: a rule firing on its own evidence is still true.
    verdict.risk = max(rule_score, model_risk)
    verdict.why = str(data.get("why") or "").strip()[:400]
    verdict.fix = str(data.get("fix") or "").strip()[:600]
    verdict.alt = str(data.get("alt") or "").strip()[:900]
    verdict.category = str(data.get("category") or "fine")
    verdict.reasons = [f.detail for f in verdict.findings]
    verdict.backend = comp.backend
    verdict.ms = comp.ms
    if verdict.risk < 45:
        verdict.fix = ""
        verdict.alt = ""
    return verdict


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def _int(v: Any, default: int) -> int:
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return default
