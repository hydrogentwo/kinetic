"""Checks that need no model, no phone and no network.

These are the tests for the parts that must be right for the thing to be
trustworthy: reading a transcript off a messy screen, and refusing to invent
anything. The model-facing half is exercised against the fixture client, which
is deliberately full of the mistakes real models make.

    second-thought check
    python -m pytest tests -q      (if pytest is around)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .apps import for_package, profile
from .config import Config, IntentConfig, ModelConfig
from .guard import review, rule_findings
from .model import Fixture, extract_json, _canned
from .reading import rank, read_conversation, Option
from .transcript import parse, render

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _cfg() -> Config:
    c = Config()
    c.model = ModelConfig(backend="fixture")
    c.intent = IntentConfig()
    return c


def _fixture() -> Fixture:
    return Fixture(str(EXAMPLES / "fixture.json"))


# ---------------------------------------------------------------- transcript


def check_chrome_is_dropped():
    read = parse({
        "app": "whatsapp", "width": 1080, "self_names": ["You"],
        "nodes": [
            {"text": "Mum"}, {"text": "online"}, {"text": "Today"},
            {"text": "12:04"}, {"text": "3 unread messages"},
            {"text": "Alice is typing…"}, {"text": "type a message"},
            {"text": "hello, are you there", "speaker": "Mum", "bounds": [0, 100, 800, 160]},
        ],
    })
    text = render(read)
    assert "online" not in text.lower(), text
    assert "typing" not in text.lower(), text
    assert "unread" not in text.lower(), text
    assert "hello, are you there" in text, text
    assert len(read.messages) == 1, read.messages
    return f"{read.dropped} chrome lines dropped, 1 message kept"


def check_sides_from_bounds():
    read = parse({
        "app": "sms", "width": 1000, "self_names": ["You"],
        "nodes": [
            {"text": "are you free later", "bounds": [0, 0, 500, 40]},
            {"text": "yeah after 6", "bounds": [420, 50, 1000, 90]},
        ],
    })
    assert [m.mine for m in read.messages] == [False, True], read.messages
    assert read.waiting_on_me is False
    assert read.trailing_mine == 1
    return "left/right bubbles assigned, trailing count 1"


def check_side_from_speaker_names():
    read = parse({
        "app": "discord",
        "nodes": [
            {"text": "Alice", "bounds": [0, 0, 100, 20]},
            {"text": "hello", "bounds": [0, 20, 400, 40]},
            {"text": "You", "bounds": [0, 40, 100, 60]},
            {"text": "hi", "bounds": [0, 60, 400, 80]},
        ],
    })
    assert read.messages[0].sender == "Alice" and not read.messages[0].mine, read.messages
    assert read.messages[1].mine, read.messages
    return "name-on-own-line attribution works"


def check_wrapped_bubble_merged():
    read = parse({
        "app": "whatsapp", "width": 1000,
        "nodes": [
            {"text": "ok so here is the thing", "bounds": [0, 0, 700, 40]},
            {"text": "i can do friday, but not before 6", "bounds": [0, 40, 700, 80]},
            {"text": "does that work for you?", "bounds": [0, 80, 700, 120]},
        ],
    })
    assert len(read.messages) == 1, read.messages
    assert "does that work for you?" in read.messages[0].text
    return "three wrapped lines merged into one message"


def check_geometry_decides_bubbles():
    wrapped = parse({"app": "whatsapp", "width": 1000, "nodes": [
        {"text": "ok so here is the thing", "bounds": [40, 0, 700, 40]},
        {"text": "i can do friday, but not before 6", "bounds": [40, 40, 700, 80]},
    ]})
    assert len(wrapped.messages) == 1, wrapped.messages
    separate = parse({"app": "whatsapp", "width": 1000, "nodes": [
        {"text": "are you coming sunday? your aunt is asking", "bounds": [40, 0, 900, 60]},
        {"text": "she wants to know how many for lunch", "bounds": [40, 74, 880, 120]},
    ]})
    assert len(separate.messages) == 2, separate.messages
    return "no gap merges, a 14px gap splits"


def check_screen_read_all_apps():
    names = ["whatsapp-family", "discord-server", "slack-work", "sms-silence", "linkedin-ask"]
    out = []
    for name in names:
        body = json.loads((EXAMPLES / f"{name}.json").read_text())
        read = parse(body, app=body.get("app"))
        assert read.messages, name
        assert all(m.text for m in read.messages), name
        assert read.app != "generic", name
        out.append(f"{read.app}:{len(read.messages)}")
    return " ".join(out)


def check_answer_reaches_the_options():
    # The prompt has to carry the state, the facts and the voice, or the model
    # is guessing. This pins the three things that were wrong in review.
    from .reading import READING_SYSTEM, _prompt
    read = parse({"app": "sms", "width": 1000, "nodes": [
        {"text": "hey", "speaker": "Sam", "bounds": [0, 0, 300, 30]},
        {"text": "you around", "mine": True, "bounds": [300, 40, 1000, 70]},
        {"text": "?", "mine": True, "bounds": [300, 80, 1000, 110]},
    ]})
    cfg = _cfg()
    prompt = _prompt(read, cfg, "buy time without vanishing", "")
    assert "You sent the last 2 message(s)" in prompt, prompt[:400]
    assert "buy time without vanishing" in prompt
    assert cfg.intent.voice[:20] in prompt
    assert "never invent facts" in READING_SYSTEM.lower()
    assert "3 or 4" in READING_SYSTEM
    return "prompt carries the state, the steer and the voice"


def check_new_speaker_breaks_merge():
    read = parse({
        "app": "discord",
        "nodes": [
            {"text": "Alice"},
            {"text": "i can do friday"},
            {"text": "Bram"},
            {"text": "i cannot"},
        ],
    })
    assert len(read.messages) == 2, read.messages
    assert read.messages[1].sender == "Bram", read.messages
    return "speaker change splits bubbles"


def check_trailing_mine_counts():
    read = parse({
        "app": "sms", "width": 1000,
        "nodes": [
            {"text": "hey", "bounds": [0, 0, 300, 40]},
            {"text": "you around?", "bounds": [300, 50, 1000, 90]},
            {"text": "?", "bounds": [300, 100, 1000, 130]},
        ],
    })
    assert read.trailing_mine == 2, read.trailing_mine
    assert read.messages[-1].text == "?", read.messages
    return "two unanswered messages detected, a lone question mark is a message"


def check_trim_keeps_recent_and_pinned():
    nodes = []
    for i in range(60):
        nodes.append({"text": f"line {i}", "speaker": "Alice" if i % 2 else "You", "bounds": [0, i, 300, i + 10]})
    read = parse({"app": "discord", "nodes": nodes}, max_turns=10, max_chars=10000)
    assert len(read.messages) <= 10, len(read.messages)
    assert "line 59" in read.messages[-1].text
    return f"capped to {len(read.messages)} of 60 messages"


def check_studio_suffix_stripped():
    read = parse({
        "app": "slack",
        "nodes": [
            {"text": "Priya Raman  9:41 AM"},
            {"text": "can you own the cutover?", "bounds": [40, 200, 1000, 240]},
            {"text": "You  9:52 AM"},
            {"text": "let me check", "bounds": [200, 300, 1080, 340]},
        ],
    })
    assert read.messages[0].sender == "Priya Raman", read.messages[0]
    assert read.messages[0].text == "can you own the cutover?", read.messages[0]
    assert read.messages[1].mine, read.messages[1]
    return "the ' 9:41 AM' suffix comes off the sender, not into the message"


def check_unwatchable_package():
    assert for_package("com.whatsapp") is not None
    assert for_package("com.example.bank") is None
    assert for_package("com.whatsapp.w4b").id == "whatsapp"
    assert profile("nonsense").id == "generic"
    return "unknown packages are refused before any read"


# ---------------------------------------------------------------- rules


def check_rule_double_text():
    read = parse({
        "app": "sms", "width": 1000,
        "nodes": [
            {"text": "hey", "speaker": "Sam", "bounds": [0, 0, 300, 30]},
            {"text": "you around", "speaker": "You", "mine": True, "bounds": [300, 40, 1000, 70]},
            {"text": "?", "speaker": "You", "mine": True, "bounds": [300, 80, 1000, 110]},
        ],
    })
    findings = rule_findings(read, "hello?", _cfg())
    rules = {f.rule for f in findings}
    assert "double_text" in rules, rules
    return f"flagged: {sorted(rules)}"


def check_rule_promise():
    read = parse({"app": "slack", "nodes": [{"text": "can you take the 14th?", "speaker": "Priya"}]})
    findings = rule_findings(read, "yes of course, consider it done", _cfg())
    assert any(f.rule == "promise" for f in findings), [f.rule for f in findings]
    return "commitment caught before the model sees it"


def check_rule_repeat_apology():
    read = parse({"app": "sms", "nodes": [
        {"text": "i'm sorry i snapped", "speaker": "Sam", "bounds": [0, 0, 300, 30]},
        {"text": "i'm sorry too", "mine": True, "bounds": [300, 40, 1000, 70]},
    ]})
    findings = rule_findings(read, "i'm really sorry again", _cfg())
    assert any(f.rule == "repeat_apology" for f in findings), [f.rule for f in findings]
    return "second apology caught"


def check_rule_heat():
    read = parse({"app": "whatsapp", "nodes": [{"text": "are you coming or not", "speaker": "Mum"}]})
    findings = rule_findings(read, "whatever. forget it.", _cfg())
    assert any(f.rule == "heat" for f in findings), [f.rule for f in findings]
    return "anger caught"


def check_rule_wall_of_text():
    read = parse({"app": "sms", "nodes": [{"text": "k", "speaker": "Sam", "bounds": [0, 0, 200, 30]}]})
    long = " ".join(f"word{i}" for i in range(80))
    findings = rule_findings(read, long, _cfg())
    assert any(f.rule == "wall_of_text" for f in findings), [f.rule for f in findings]
    return "paragraph in a one-word thread caught"


def check_rules_are_quiet_on_fine_drafts():
    read = parse({"app": "whatsapp", "nodes": [
        {"text": "are you coming sunday?", "speaker": "Mum", "bounds": [0, 0, 800, 40]},
    ]})
    findings = rule_findings(read, "yeah i'll be there, just me", _cfg())
    assert not findings, [f.rule for f in findings]
    return "a normal answer produces no findings"


class Stub:
    """A client that answers with exactly what a test wants to see."""

    name = "stub"

    def __init__(self, payload: dict):
        self.payload = payload

    def structured(self, messages, schema, **kw):
        from .model import Completion
        return dict(self.payload), Completion(text="{}", backend="stub", ms=3)

    def health(self):
        return True, "stub"


def check_guard_is_quiet_below_threshold():
    read = parse({"app": "discord", "nodes": [
        {"text": "Alice"}, {"text": "who's on tonight"}, {"text": "You"}, {"text": "me"},
    ]})
    quiet = Stub({"risk": 8, "why": "", "reasons": [], "category": "fine"})
    v = review(quiet, read, "yeah i'm in", _cfg())
    assert v.risk < 35, v.risk
    assert not v.fix and not v.alt, v.as_dict()
    return f"risk {v.risk}, no rewrite offered, below the 35 threshold"


def check_guard_takes_the_higher_of_rules_and_model():
    # The model says fine; a rule has real evidence. The rule wins.
    read = parse({"app": "sms", "width": 1000, "nodes": [
        {"text": "hey", "speaker": "Sam", "bounds": [0, 0, 300, 30]},
        {"text": "you around", "mine": True, "bounds": [300, 40, 1000, 70]},
        {"text": "?", "mine": True, "bounds": [300, 80, 1000, 110]},
    ]})
    lenient = Stub({"risk": 5, "why": "looks fine", "reasons": [], "category": "fine"})
    v = review(lenient, read, "hey sorry to bother you again but are you there", _cfg())
    assert v.risk >= 50, v.as_dict()
    assert any(f.rule == "double_text" for f in v.findings)
    return f"rule evidence outranked a lenient model ({v.risk})"


def check_guard_error_degrades_to_rules():
    read = parse({"app": "slack", "nodes": [{"text": "can you take the 14th?", "speaker": "Priya"}]})
    v = review(Missing(), read, "yes of course, consider it done", _cfg())
    assert v.error, v.as_dict()
    assert v.risk >= 50, v.risk
    assert any(f.rule == "promise" for f in v.findings)
    return "no model: rules still score the draft and the error is reported"


def check_guard_surfaces_and_rewrites():
    read = parse({"app": "sms", "nodes": [
        {"text": "i'm sorry i snapped", "speaker": "Sam", "bounds": [0, 0, 800, 40]},
        {"text": "i'm sorry too", "mine": True, "bounds": [300, 60, 1000, 90]},
        {"text": "anyway. are we ok?", "mine": True, "bounds": [300, 100, 1000, 130]},
    ]})
    v = review(_fixture(), read, "i'm sorry, i know i keep saying i'm sorry - call me? "
                                 "I promise I won't bring any of it up again", _cfg())
    assert v.risk >= 65, v.risk
    assert v.alt or v.fix, v.as_dict()
    assert v.reasons, v.as_dict()
    return f"risk {v.risk}, {len(v.reasons)} reasons, rewrite offered"


# ---------------------------------------------------------------- options


def check_option_filtering():
    cfg = _cfg()
    read = parse({"app": "sms", "nodes": [
        {"text": "k", "speaker": "Sam", "bounds": [0, 0, 200, 30]},
    ]})
    opts = [
        Option("hi [your name here]", "answer", risk=5),
        Option("ok", "answer", risk=5),
        Option("ok", "close_out", risk=5),
        Option("k", "answer", risk=5),
        Option("a" * 800, "warm", risk=5),
        Option("yeah, sunday works", "answer", risk=5, why="answers it"),
    ]
    ranked = rank(opts, read, cfg, exclude=[])
    texts = [o.text for o in ranked]
    assert not any("[" in t for t in texts), texts            # unsendable, dropped
    assert texts.count("ok") == 1, texts                       # exact duplicate, dropped
    assert any(len(t) > 700 for t in texts), texts             # demoted, not hidden
    assert ranked[0].text != "a" * 800, "the wall of text must not rank first"
    assert any(o.notes for o in ranked), "penalties are explained, not silent"
    return f"{len(opts)} offered, {len(ranked)} shown, notes on {sum(1 for o in ranked if o.notes)}"


def check_reading_end_to_end():
    body = json.loads((EXAMPLES / "whatsapp-family.json").read_text())
    read = parse(body, app=body["app"])
    res = read_conversation(_fixture(), read, _cfg())
    assert res.situation, res.as_dict()
    assert 1 <= len(res.options) <= 4, len(res.options)
    assert all(o.text and o.why for o in res.options), [o.as_dict() for o in res.options]
    assert len({o.move for o in res.options}) == len(res.options), "moves should be distinct"
    return f"{len(res.options)} options, moves {[o.move for o in res.options]}"


def check_no_model_still_useful():
    cfg = _cfg()
    read = parse({"app": "sms", "nodes": [
        {"text": "are you free later", "speaker": "Sam", "bounds": [0, 0, 800, 40]},
    ]})
    res = read_conversation(Missing(), read, cfg)
    assert res.error, res.as_dict()
    assert res.situation, "must still say what is happening"
    assert res.options, "must still offer something safe"
    return f"degraded cleanly: {res.options[0].text!r}"


class Missing:
    name = "missing"

    def structured(self, *a, **k):
        from .model import ModelError
        raise ModelError("no model here")

    def health(self):
        return False, "no model here"


# ---------------------------------------------------------------- json repair


def check_json_repair():
    cases = [
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here you go:\n{"a": 1, "b": [2]}', {"a": 1, "b": [2]}),
        ('{"a": 1,}', {"a": 1}),
        ('{a: 1}', {"a": 1}),
        ('\u201canswer\u201d: {"risk": 3}', {"risk": 3}),
        ('[{"a": 2}]', {"a": 2}),
    ]
    for raw, want in cases:
        got = extract_json(raw)
        assert got == want, (raw, got, want)
    try:
        extract_json("no json at all")
    except ValueError:
        pass
    else:
        raise AssertionError("garbage should not parse")
    return f"{len(cases)} malformed answers repaired"


def check_leaked_paths_are_ignored():
    # A sign-in dialog above the chat is a node list too. The conversation is
    # still readable from it, and the draft does not become the password field.
    read = parse({"app": "whatsapp", "width": 1000, "nodes": [
        {"text": "Sign in to iCloud", "bounds": [0, 0, 1000, 60]},
        {"text": "Password", "editable": True, "bounds": [0, 80, 1000, 120]},
        {"text": "your mum's chat", "speaker": "Mum", "bounds": [40, 300, 800, 340]},
        {"text": "type a message"},
    ]})
    # A password field is never a message, and the chat is still readable.
    assert all("password" not in m.text.lower() for m in read.messages), read.messages
    assert read.draft == "Password", read.draft
    assert any("your mum's chat" in m.text for m in read.messages), read.messages
    assert all("Sign in" not in m.text for m in read.messages if "mum" in m.text), read.messages
    return "the compose box is captured, a password field is not, chat intact"


def check_truncated_transcript_is_flagged():
    from .reading import _prompt
    nodes = [{"text": f"message number {i}", "speaker": "Alice", "bounds": [40, i * 40, 900, i * 40 + 30]}
             for i in range(80)]
    read = parse({"app": "discord", "nodes": nodes}, max_turns=10)
    prompt = _prompt(read, _cfg(), "", "")
    assert "10 visible messages" in prompt, prompt[:200]
    assert "message number 79" in prompt
    assert "message number 0" not in prompt
    return "a long thread is capped, and the prompt says how many it saw"


def check_fixture_fallback_is_defensible():
    out = _canned("Them: hey\nYou: hi")
    assert out["options"], out
    assert all("[" not in o["text"] for o in out["options"]), out
    return "offline answers contain no placeholders"





def run_checks(verbose: bool = True) -> int:
    failed = 0
    for fn in CHECKS:
        name = fn.__name__.removeprefix("check_")
        try:
            detail = fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        else:
            if verbose:
                print(f"ok   {name}: {detail}")
    total = len(CHECKS)
    print()
    print(f"{total - failed}/{total} checks passed")
    return 1 if failed else 0


# ---------------------------------------------------------------- the real HTTP path

def check_openai_client_against_a_stub_server():
    """Exercise the code that will talk to `kinetic serve`, including its two
    fallback paths. A stub stands in so this runs with no model and no GPU."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from .config import ModelConfig
    from .model import OpenAI
    from .reading import read_conversation

    seen = {"schema": 0, "plain": 0, "thinking_off": 0, "auth": None}

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def _send(self, obj, code=200):
            data = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send({"object": "list", "data": [{"id": "LFM3-1.7B", "context_length": 8192}]})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = _json.loads(self.rfile.read(n) or b"{}")
            seen["auth"] = self.headers.get("Authorization")
            if body.get("chat_template_kwargs", {}).get("enable_thinking") is False:
                seen["thinking_off"] += 1
            if body.get("response_format"):
                seen["schema"] += 1
                # Behave like a server built before json_schema support.
                return self._send({"error": "response_format not supported"}, 400)
            seen["plain"] += 1
            payload = {
                "intent": {"situation": "Stub situation.", "what_they_want": "w",
                           "what_you_want": "y", "stakes": "low"},
                "options": [{"text": "stub answer", "move": "answer", "tone": "plain",
                             "why": "it is a stub", "risk": 5}],
                "suggested_intents": [],
            }
            self._send({
                "choices": [{"message": {"role": "assistant",
                                         "content": _json.dumps(payload)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        cfg = _cfg()
        cfg.model = ModelConfig(base_url=f"http://127.0.0.1:{port}/v1", model="auto")
        client = OpenAI(cfg.model)
        ok, why = client.health()
        assert ok and "LFM3-1.7B" in why, why

        read = parse({"app": "sms", "nodes": [
            {"text": "are you around", "speaker": "Sam", "bounds": [0, 0, 800, 40]},
        ]})
        res = read_conversation(client, read, cfg)
        assert res.backend == "openai", res.backend
        assert res.options and res.options[0].text == "stub answer", res.as_dict()
        assert seen["thinking_off"] == 2, seen   # schema attempt + plain retry
        assert seen["schema"] == 1 and seen["plain"] == 1, seen
        assert seen["auth"] == "Bearer local", seen
        # The schema attempt failed, so the client must stop asking rather than
        # pay for a failed round trip on every future call.
        assert cfg.model.json_schema is False, "should stop asking after a refusal"
        return f"schema refused -> retried plain, thinking off, {seen['plain']} plain call"
    finally:
        httpd.shutdown()
        httpd.server_close()


def check_openai_client_reports_a_dead_model():
    from .config import ModelConfig
    from .model import ModelError, OpenAI

    client = OpenAI(ModelConfig(base_url="http://127.0.0.1:1/v1", timeout_s=2))
    ok, why = client.health()
    assert not ok and "cannot reach" in why, why
    read = parse({"app": "sms", "nodes": [{"text": "hi", "speaker": "Sam"}]})
    from .reading import read_conversation
    res = read_conversation(client, read, _cfg())
    assert res.error and res.options, res.as_dict()
    return "a dead endpoint degrades to rules, and says so"


CHECKS = [
    check_studio_suffix_stripped,
    check_geometry_decides_bubbles,
    check_screen_read_all_apps,
    check_answer_reaches_the_options,
    check_chrome_is_dropped,
    check_sides_from_bounds,
    check_side_from_speaker_names,
    check_wrapped_bubble_merged,
    check_new_speaker_breaks_merge,
    check_trailing_mine_counts,
    check_trim_keeps_recent_and_pinned,
    check_unwatchable_package,
    check_rule_double_text,
    check_rule_promise,
    check_rule_repeat_apology,
    check_rule_heat,
    check_rule_wall_of_text,
    check_rules_are_quiet_on_fine_drafts,
    check_guard_is_quiet_below_threshold,
    check_guard_surfaces_and_rewrites,
    check_option_filtering,
    check_reading_end_to_end,
    check_no_model_still_useful,
    check_json_repair,
    check_leaked_paths_are_ignored,
    check_truncated_transcript_is_flagged,
    check_fixture_fallback_is_defensible,
    check_openai_client_against_a_stub_server,
    check_openai_client_reports_a_dead_model,
]
