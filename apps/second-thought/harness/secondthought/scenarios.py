"""The five screens the demo page shows.

Each one is a real example of something the overlay has to get right: a family
group where a deadline has already been set, a Discord server with bystanders,
work, a thread that has gone quiet, and a message from someone the person does
not know well. They are sent to ``/read`` as the same node list the Android app
would extract, so the demo exercises the real path rather than a mock.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .transcript import parse

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

FILES: list[tuple[str, str, str]] = [
    ("whatsapp-family.json", "Family · WhatsApp",
     "A deadline is already in the thread and the ask is a number, not a maybe."),
    ("discord-server.json", "Server · Discord",
     "Six people in the channel, one of them waiting on you tonight."),
    ("slack-work.json", "Work · Slack",
     "A favour dressed as a small one. The draft in the box says yes to all of it."),
    ("sms-silence.json", "Silence · Messages",
     "Three messages into the night with no reply. The draft gets worse."),
    ("linkedin-ask.json", "Stranger · LinkedIn",
     "Someone you met once, hiring. The register is the whole problem."),
]


def all_scenarios() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, label, blurb in FILES:
        path = EXAMPLES / name
        if not path.exists():
            continue
        screen = json.loads(path.read_text(encoding="utf-8"))
        parsed = parse(screen, app=screen.get("app"))
        out.append({
            "id": name.rsplit(".", 1)[0],
            "app_id": screen.get("app") or "generic",
            "label": label,
            "blurb": blurb,
            "screen": {
                **screen,
                "app_label": parsed.profile.label,
                "messages": [m.as_dict() | {"mine": m.mine} for m in parsed.messages],
                "read": {
                    "messages": len(parsed.messages),
                    "dropped": parsed.dropped,
                    "trailing_mine": parsed.trailing_mine,
                    "waiting_on_me": parsed.waiting_on_me,
                },
            },
        })
    return out
