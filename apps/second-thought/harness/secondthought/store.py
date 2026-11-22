"""Local history, and the small amount of learning that comes from it.

Nothing here leaves the phone or the laptop. The database is one SQLite file
next to the config.

What is stored:

* ``readings`` - each time the overlay read a thread and offered options, with
  the app, the people, the situation line and the options it offered.
* ``choices``  - which option was copied or sent, and which were ignored. This is
  the only signal used to change behaviour.
* ``guards``   - every draft that was scored at or above the display threshold,
  so the person can see their own patterns ("you do this most on Sundays").

What is deliberately *not* stored: the text of messages the person typed or
received, beyond what is needed to show the last few readings in the history
view. ``store_history: false`` turns all of this off.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    app TEXT, thread TEXT, people TEXT,
    situation TEXT, backend TEXT,
    options TEXT
);
CREATE TABLE IF NOT EXISTS choices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    reading_id INTEGER,
    app TEXT, move TEXT, risk INTEGER, text TEXT,
    action TEXT,
    FOREIGN KEY (reading_id) REFERENCES readings(id)
);
CREATE TABLE IF NOT EXISTS guards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    app TEXT, people TEXT,
    risk INTEGER, category TEXT, reasons TEXT,
    sent INTEGER DEFAULT 0,
    followed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS choices_app ON choices(app, move);
CREATE INDEX IF NOT EXISTS guards_ts ON guards(ts);
"""


@dataclass
class Stats:
    """What the history says about this person, in a few numbers."""

    choices: int = 0
    by_move: dict[str, int] = None  # type: ignore[assignment]
    risk_taken: float = 0.0
    nudges: int = 0
    nudges_followed: int = 0

    def __post_init__(self) -> None:
        if self.by_move is None:
            self.by_move = {}

    @property
    def follow_rate(self) -> float:
        if not self.nudges:
            return 0.0
        return self.nudges_followed / self.nudges

    def as_dict(self) -> dict[str, Any]:
        return {
            "choices": self.choices,
            "by_move": dict(sorted(self.by_move.items(), key=lambda kv: -kv[1])),
            "nudges": self.nudges,
            "nudges_followed": self.nudges_followed,
            "follow_rate": round(self.follow_rate, 3),
            "average_risk_taken": round(self.risk_taken, 1),
        }


class Store:
    def __init__(self, path: Path | str, enabled: bool = True):
        self.enabled = enabled
        self.conn: sqlite3.Connection | None = None
        if not enabled:
            return
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        # Databases written before `followed` existed.
        try:
            self.conn.execute("ALTER TABLE guards ADD COLUMN followed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    # -- writes ----------------------------------------------------------

    def record_reading(self, reading: Any, read: Any) -> int:
        if not self.conn:
            return 0
        cur = self.conn.execute(
            "INSERT INTO readings (ts, app, thread, people, situation, backend, options) VALUES (?,?,?,?,?,?,?)",
            (time.time(), reading.app, reading.thread, ", ".join(read.others()),
             reading.situation, reading.backend,
             json.dumps([o.as_dict() for o in reading.options], ensure_ascii=False)[:20000]),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def record_choice(self, *, reading_id: int, app: str, option: Any, action: str = "copy",
                      people: Iterable[str] = ()) -> None:
        if not self.conn:
            return
        self.conn.execute(
            "INSERT INTO choices (ts, reading_id, app, move, risk, text, action) VALUES (?,?,?,?,?,?,?)",
            (time.time(), reading_id, app, getattr(option, "move", ""),
             getattr(option, "adjusted", 0) or getattr(option, "risk", 0),
             getattr(option, "text", "")[:2000], action),
        )
        self.conn.commit()

    def record_guard(self, *, app: str, people: Iterable[str], verdict: Any, sent: bool = False) -> None:
        if not self.conn:
            return
        self.conn.execute(
            "INSERT INTO guards (ts, app, people, risk, category, reasons, sent) VALUES (?,?,?,?,?,?,?)",
            (time.time(), app, ", ".join(people), int(verdict.risk), verdict.category,
             json.dumps(verdict.reasons, ensure_ascii=False)[:4000], 1 if sent else 0),
        )
        self.conn.commit()

    def record_followed(self, app: str = "") -> bool:
        """The person took the rewrite instead of their own draft.

        This is the only measure of whether the nudge actually changes anything,
        as opposed to whether it appears, so it only counts when they used the
        suggested text rather than simply not sending at all.
        """
        if not self.conn:
            return False
        row = self.conn.execute(
            "SELECT id FROM guards WHERE followed = 0 AND (? = '' OR app = ?) "
            "ORDER BY id DESC LIMIT 1", (app, app)).fetchone()
        if not row:
            return False
        self.conn.execute("UPDATE guards SET followed = 1 WHERE id = ?", (row["id"],))
        self.conn.commit()
        return True

    # -- reads -----------------------------------------------------------

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.conn:
            return []
        rows = self.conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": r["id"],
                "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])),
                "app": r["app"],
                "thread": r["thread"],
                "people": r["people"],
                "situation": r["situation"],
                "backend": r["backend"],
                "options": json.loads(r["options"] or "[]"),
            }
            for r in rows
        ]

    def stats(self) -> Stats:
        s = Stats()
        if not self.conn:
            return s
        for r in self.conn.execute("SELECT move, risk, COUNT(*) c FROM choices GROUP BY move, risk"):
            s.by_move[r["move"] or "?"] = s.by_move.get(r["move"] or "?", 0) + r["c"]
            s.choices += r["c"]
            s.risk_taken += float(r["risk"] or 0) * r["c"]
        if s.choices:
            s.risk_taken /= s.choices
        row = self.conn.execute(
            "SELECT COUNT(*) c, SUM(followed) f FROM guards").fetchone()
        s.nudges = row["c"] or 0
        s.nudges_followed = row["f"] or 0
        return s

    def patterns(self, limit: int = 6) -> list[dict[str, Any]]:
        """The nudges that fire most, for the "your patterns" panel."""
        if not self.conn:
            return []
        rows = self.conn.execute(
            "SELECT category, app, COUNT(*) c, AVG(risk) r FROM guards "
            "GROUP BY category, app ORDER BY c DESC LIMIT ?", (limit,)).fetchall()
        return [
            {"category": r["category"], "app": r["app"], "count": r["c"], "average_risk": round(r["r"] or 0, 1)}
            for r in rows
        ]

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None


class NullStore(Store):
    def __init__(self) -> None:
        super().__init__(":memory:", enabled=False)
