"""``second-thought`` - the brain behind the overlay.

`second-thought serve` starts the local brain and the demo page.

    second-thought init                 write a config to edit
    second-thought serve                serve on 127.0.0.1:8787
    second-thought serve --lan          also on the network, with a pairing token
    second-thought read FILE.json       read a saved screen and print the options
    second-thought guard FILE.json      score the draft in FILE.json
    second-thought check                hardware-free self-check of the whole path
    second-thought profile              what is watchable, and the rules per app
    second-thought intent add "..."     add a standing intention

``FILE.json`` is what the overlay POSTs to ``/read``; see ``examples/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import apps as apps_mod
from .config import Config, ensure_init
from .guard import review
from .model import ModelError, client as make_client
from .server import run
from .transcript import parse, render

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _client(cfg: Config, fixture: str | None = None):
    path = fixture or (str(EXAMPLES / "fixture.json") if cfg.model.backend == "fixture" else None)
    return make_client(cfg.model, path)


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"no such file: {p}")
    raw = p.read_text(encoding="utf-8")
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        val = {"nodes": [{"text": line} for line in raw.splitlines()]}
    return val if isinstance(val, dict) else {"nodes": val}


def cmd_init(args) -> int:
    path = ensure_init()
    print(f"config at {path}")
    print("edit it, or use `second-thought intent add ...` from the overlay")
    return 0


def cmd_serve(args) -> int:
    cfg = Config.load()
    c = _client(cfg, args.fixture)
    from .server import serve
    from .store import Store
    httpd = serve(cfg, c, host=args.host, port=args.port, lan=args.lan,
                  quiet=not args.verbose, fixture=Path(args.fixture) if args.fixture else None)
    host, actual = httpd.server_address[:2]
    base = f"http://127.0.0.1:{actual}"
    print(f"second-thought listening on {base}  (bound {host})")
    print(f"  demo:   {base}/")
    print(f"  health: {base}/health")
    if cfg.server.token:
        from .server import local_ip
        print(f"  phone:  http://{local_ip()}:{actual}   token: {cfg.server.token}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def cmd_read(args) -> int:
    cfg = Config.load()
    c = _client(cfg, args.fixture)
    body = _load(args.file)
    read = parse(body, app=body.get("app"), draft=body.get("draft"),
                 max_turns=cfg.watch.max_turns, max_chars=cfg.watch.max_chars)
    from .reading import read_conversation
    res = read_conversation(c, read, cfg, steer=str(body.get("steer") or ""),
                            draft=read.draft)
    print(f"# {read.profile.label} · {read.thread or 'direct'} · {len(read.messages)} messages read")
    print()
    print("TRANSCRIPT THE MODEL SAW")
    print(render(read) or "(nothing)")
    print()
    print("READING")
    print(f"  what is happening: {res.situation}")
    print(f"  what they want:    {res.what_they_want}")
    print(f"  what you want:     {res.what_you_want}")
    print(f"  stakes:            {res.stakes}")
    for n in res.noticed:
        print(f"  noticed:           {n}")
    print()
    print("WHAT YOU COULD SEND")
    for i, o in enumerate(res.options, 1):
        print(f"  {i}. {o.text}")
        print(f"     [{o.move_label} · risk {o.adjusted}] {o.why}")
        for note in o.notes:
            print(f"     ! {note}")
    for s in res.suggested_intents:
        print(f"  + intention you might add: {s}")
    print()
    print(f"(backend: {res.backend}, {res.ms} ms" + (f", error: {res.error}" if res.error else "") + ")")
    return 0 if not res.error else 1


def cmd_guard(args) -> int:
    cfg = Config.load()
    c = _client(cfg, args.fixture)
    body = _load(args.file)
    read = parse(body, app=body.get("app"), max_turns=cfg.watch.max_turns)
    draft = str(body.get("draft") or "")
    if not draft:
        raise SystemExit("the file needs a \"draft\" field")
    v = review(c, read, draft, cfg)
    print(f"DRAFT: {draft}")
    print(f"RISK:  {v.risk}  ({v.category})" + ("  - worth showing" if v.risk >= cfg.guard.min_risk else "  - say nothing"))
    for r in v.reasons:
        print(f"  - {r}")
    if v.why:
        print(f"  why: {v.why}")
    if v.fix:
        print(f"  fix: {v.fix}")
    if v.alt:
        print(f"  alt: {v.alt}")
    if v.error:
        print(f"  (model error: {v.error})")
    return 0


def cmd_check(args) -> int:
    """End to end with no model and no phone. What CI runs."""
    from .checks import run_checks
    return run_checks(verbose=not args.quiet)


def cmd_profile(args) -> int:
    cfg = Config.load()
    print("watchable apps:")
    for p in apps_mod.APPS.values():
        if p.id == "generic":
            continue
        watching = "watching" if p.id in cfg.watch.apps else "not watching"
        print(f"  {p.label:<12} {p.id:<10} {watching:<13} {', '.join(p.android_packages)}")
        print(f"               {p.register}")
    print()
    print("packages the phone may read (paste into the app, this is the allowlist):")
    for pkg in apps_mod.allowlist(cfg.watch.apps):
        print(f"  {pkg}")
    print()
    print(f"guard: {cfg.guard.strength}, shows at risk >= {cfg.guard.min_risk}, never sends: {cfg.guard.never_send}")
    return 0


def cmd_intent(args) -> int:
    cfg = Config.load()
    if args.action == "add" and args.text:
        cfg.intent.goals.append(args.text)
        cfg.save()
        print(f"added. {len(cfg.intent.goals)} intentions now:")
    elif args.action == "remove" and args.text:
        before = len(cfg.intent.goals)
        cfg.intent.goals = [g for g in cfg.intent.goals if args.text.lower() not in g.lower()]
        cfg.save()
        print(f"removed {before - len(cfg.intent.goals)}")
    elif args.action == "avoid" and args.text:
        cfg.intent.avoid.append(args.text)
        cfg.save()
        print(f"added to avoid list: {args.text}")
    for g in cfg.intent.goals:
        print(f"  - {g}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="second-thought", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="write a default config")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("serve", help="run the local brain and the demo page")
    s.add_argument("--lan", action="store_true", help="also listen on the network, with a token")
    s.add_argument("--host", default=None, help="bind address (default 127.0.0.1; 0.0.0.0 for all)")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--fixture", default=None, help="canned answers instead of a model")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_serve)

    for name, fn in (("read", cmd_read), ("guard", cmd_guard)):
        s = sub.add_parser(name, help=f"{name} a saved screen read")
        s.add_argument("file")
        s.add_argument("--fixture", default=None)
        s.set_defaults(fn=fn)

    s = sub.add_parser("check", help="self-check the whole path with no model")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("profile", help="what is watchable")
    s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("intent", help="add or remove a standing intention")
    s.add_argument("action", choices=["list", "add", "remove", "avoid"])
    s.add_argument("text", nargs="?", default="")
    s.set_defaults(fn=cmd_intent)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except ModelError as e:
        print(f"model error: {e}", file=sys.stderr)
        print("hint: is `kinetic serve model.gguf` running? or try --fixture", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
