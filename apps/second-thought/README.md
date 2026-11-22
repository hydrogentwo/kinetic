# Second Thought

A floating panel that sits over the messaging app you already have open, reads
the conversation, works out what you are actually trying to do, and puts two or
three things you could send in front of you — in your voice. When you are about
to send something you will regret, it says so, quietly, and offers the version
you would have written in the morning.

Nothing leaves your machine. The model runs on your own computer through
[Kinetic](../..), and the phone talks to it over your own wifi.

```
   phone                          your laptop
┌─────────────────────┐        ┌──────────────────────────┐
│  floating panel     │  http  │  second-thought serve    │
│  ├─ what's going on │ ─────► │  ├─ reads the transcript │
│  ├─ 3 options       │        │  ├─ works out the intent │
│  └─ the nudge       │ ◄───── │  ├─ writes in your voice │
│                     │        │  └─ guards your drafts   │
│  accessibility read │        │        kinetic serve     │
└─────────────────────┘        └──────────────────────────┘
```

## Why this shape

Reading the screen has to happen on the phone: no web page can see Discord's
window, and no server can either. Understanding the conversation should not
happen on someone else's computer, because the conversation is the private
thing. So the phone does the reading and the rendering, and the laptop does the
thinking.

That split is also why the panel is a WebView pointed at the laptop: the demo
page, the overlay and the tests all run exactly the same reading code, so what
you see in the browser is what lands on the phone.

## The three things it does

**Reads the conversation properly.** Not the last message — the whole screen.
Which lines are messages and which are app furniture, who is speaking, which
bubbles are yours, where one message ends and the next begins when the bubble
wrapped, how many messages you have sent into silence, and what the norms of
*this* app are (a Discord server is not a Slack channel is not a text to your
mum). See `harness/secondthought/transcript.py` and `apps.py`.

**Works out what you mean.** Every reading names the situation, what they want,
what you want, the stakes, and what is not in the transcript that matters. Then
it offers options that each do a *different* thing — answer, buy time, decline,
set a boundary, be warm, close it out — because four rephrasings of one sentence
is worse than one good line. You can add intentions at any time ("just say yes
to family things", "after 11pm, wait until morning") and every suggestion
follows them. It also suggests intentions back, when it notices a pattern.

**Stops the stupid message.** A rules layer that runs offline and catches what a
model cannot know from the draft alone: this is the fourth message into silence,
you already apologised, this promises a week you have not looked at, the thread
has been one-liners and this is a paragraph. Then a model layer that reads the
person and the room. The nudge never blocks and never sends. It is silent unless
something is actually wrong — no green ticks, no "all clear".

## Quick start

Two terminals on the laptop.

```sh
# 1. a model. Any small instruct model works; this one is quick on a CPU.
kinetic pull LiquidAI/LFM3-1.7B-GGUF:Q4_K_M
kinetic serve LiquidAI/LFM3-1.7B-GGUF:Q4_K_M -c 8192 --port 8080

# 2. the brain. --lan so the phone can reach it; it prints a token and a QR.
cd apps/second-thought/harness
python3 -m secondthought serve --lan
```

Open the demo page it prints — `http://127.0.0.1:8787/` — and the whole thing
runs in a browser against five real scenarios. Nothing needs a phone.

Then build and install the app:

```sh
cd apps/second-thought/android
./gradlew installDebug      # needs the Android SDK; see android/README.md
```

Open Second Thought and turn on the four switches in order. The last one is the
address and token the harness printed.

## No model? Still fine

```sh
python3 -m secondthought serve --fixture examples/fixture.json
```

Every path works with canned answers. The demo is honest about it — the status
pill says `demo mode · no model` — and the guard still fires, because its rules
run offline. If the model goes away mid-session, `/read` degrades to the facts it
can state without one rather than failing.

## Commands

```sh
python3 -m secondthought check                    # 24 checks, no model, no phone
python3 -m secondthought read examples/slack-work.json
python3 -m secondthought guard examples/sms-silence.json
python3 -m secondthought profile                  # what is watchable, per-app rules
python3 -m secondthought intent add "one apology per argument"
```

`read` and `guard` take the same JSON the phone posts; a plain text file works
too, one message per line.

## The API the phone uses

Local, JSON, all of it on loopback unless you pass `--lan`.

| Route | What it does |
|---|---|
| `GET /health` | which model, which backend, alive |
| `GET /profile` | watchable apps, per-app norms, the guard's settings |
| `GET /intent`, `POST /intent` | read and change the standing intentions |
| `POST /read` | a screen read → the transcript, a reading, and options |
| `POST /guard` | a draft → a verdict, in under a second |
| `POST /feedback` | which option you took (this is the only learning signal) |
| `GET /history`, `GET /stats` | what it has shown you, and your patterns |

## What it will not do

- It will not send anything. There is no code path that presses send, and the
  accessibility service has no gesture permission.
- It will not type into your message box or edit your draft. It offers a line to
  copy.
- It will not read an app you did not approve. The package name is checked
  before a node is touched, in both the phone and the brain.
- It will not read passwords, or anything you are typing into a keyboard it does
  not own.
- It will not talk to a server. The only network call in the app is to the
  address you paired.

## What to be clear-eyed about

Accessibility-based screen reading is the only way to do this on Android, and it
is the same mechanism abusive apps use. Three honest consequences:

1. **The conversation is on your laptop in memory, and in a local SQLite file if
   `store_history` is on.** Set it to `false` and there is no record beyond the
   model's context window.
2. **`--lan` puts the brain on your network.** Anything on that wifi that has
   the token can read the same endpoint. The token is required, but treat the
   network as untrusted: it is a home wifi feature, not a coffee-shop one.
3. **The transcript is whatever is on screen.** Scroll back and the harness sees
   more; leave it at the bottom and it sees less. Every prompt says so, and the
   model is told not to claim knowledge it does not have.

## Layout

```
apps/second-thought/
├── harness/                    the brain (Python, no dependencies)
│   ├── secondthought/
│   │   ├── transcript.py       screen nodes -> a conversation
│   │   ├── apps.py             what each app's norms are
│   │   ├── reading.py          intent + distinct options
│   │   ├── guard.py            rules first, then judgement
│   │   ├── model.py            kinetic, or canned answers, or nothing
│   │   ├── server.py           the local HTTP API
│   │   ├── scenarios.py        the five demo screens
│   │   ├── checks.py           the 24 checks CI runs
│   │   └── web/                the demo page and the floating panel
│   └── examples/               screen fixtures and canned answers
└── android/                    the overlay (Kotlin)
    └── app/src/main/java/dev/veloresearch/secondthought/
        ├── A11yService.kt      watches the foreground app, debounces, reads
        ├── ScreenReader.kt     accessibility tree -> node list
        ├── Apps.kt             the allowlist, on the phone
        ├── Brain.kt            the link to the laptop
        ├── OverlayService.kt   the floating window
        └── MainActivity.kt     setup, and the four switches
```

## Tests

```sh
cd apps/second-thought/harness
python3 -m secondthought check
```

Twenty-four checks over the parts that have to be right: chrome dropped, sides
assigned, wrapped bubbles merged, a lone `?` treated as a message rather than
decoration, the quiet-draft case staying quiet, a lenient model being overruled
by a rule with real evidence, every option being sendable, and six kinds of
malformed JSON repaired. They run with no model and no phone, in under a second,
which is why they can run on every commit.

## Status

0.1.0. The brain is finished and tested; the Android app compiles against SDK 35
and needs a real device to shake out per-app accessibility trees, which differ
from app to app and version to version. Expect the first run on Discord to need a
tweak to `ScreenReader.kt`; `python3 -m secondthought read` is how you check what
it saw without touching the phone.
