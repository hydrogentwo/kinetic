# Design notes

Why it is built this way, and what it is not.

## The split: phone reads, laptop thinks

Reading the screen is only possible on the device with the screen. Understanding
it should only happen on a machine you own. So:

- **Phone** — accessibility tree → a flat node list → the brain. Renders the
  panel. Never thinks, never stores anything but the pairing address.
- **Laptop** — parses the node list into a conversation, reads it, writes
  options, scores drafts, keeps history. Runs `kinetic serve`.

This means the phone works on any Android 10+ device with no model on it, and
the same brain can serve a tablet, a desktop client, or a browser demo. It also
means there is exactly one place where private text exists in a way that survives
the moment: the laptop, under the person's control, deletable with one file.

## Reading a screen without trusting it

The accessibility tree is not a conversation. It is a flat list of whatever the
app decided to expose, in whatever order, with whatever labels. `transcript.py`
exists because everything downstream is only as good as this step, and it is
almost entirely rules, not model:

- **Chrome vs speech.** `Today`, `online`, `delivered`, `Jules is typing…`,
  `message #general`, `12:04`. Dropped by exact match and by pattern. A lone `?`
  is *not* chrome — it is the most anxious message in the thread and misreading
  it inverts the whole reading.
- **Sides.** A bubble hugging the right edge is yours, one hugging the left is
  theirs. The edge is measured, not the start x, because a long message of yours
  can begin at 25% of the width. Speaker names (`You`, `Me`) override it, and
  explicit `mine` from the phone overrides both.
- **Bubble boundaries.** Two nodes are one message when they are on the same
  side, from the same person, aligned, and the second starts where the first
  stopped. A wrapped line has no gap; two bubbles always do. Without geometry,
  it falls back to punctuation.
- **Senders.** Discord and Slack print a name on its own line above the message.
  A short line that is not punctuation and starts with a capital is a sender, and
  it applies to the next message only.
- **The tail.** `trailing_mine` counts unanswered messages in a row. It is the
  single most useful fact for both halves: the reading uses it to decide whether
  the thread needs an answer or needs to be left alone, and the guard uses it to
  notice the fourth message into silence.

Every one of these has a check in `checks.py`, because a regression here makes
the model confidently wrong rather than obviously broken.

## Options, not replies

The obvious build is "generate a reply". It is worse, for two reasons.

A single reply is a decision made for the person. What they need is the shape of
the space: yes, no, later, and what each costs. So every option names its
**move** and its **risk**, and the model is instructed to make them different. A
suggestion the person rejects is useful; a suggestion they never see is not.

And a single reply invites the model to invent. Facts about the thread, plans,
times, feelings, shared history — all of it is easy to hallucinate and impossible
to un-send. The prompt forbids it explicitly, options with placeholders are
dropped as unsendable, and the offline filter demotes anything that does not fit
the thread's own register.

## The guard: rules first, judgement second

The rules are the part that cannot be done by the model, in either direction:

- They see things the model cannot: how many messages in a row, what this person
  said an hour ago, whether an apology already happened, the stated avoid-list.
- They are free and instant, which matters because the check runs while somebody
  is typing.
- They are auditable. "You have sent three messages in a row" is a fact, not an
  opinion.

The model adds what rules cannot: whether *this* draft, in *this* thread, in
*this* app, would actually land badly. Its score and the rules' score are
combined by taking the higher, deliberately: a lenient model must not be able to
overrule a rule with real evidence, and a rule with no evidence must not make the
model's judgement irrelevant.

The thresholds are opinionated. Below 35: say nothing, and mean it — the most
common correct output is silence. 35-64: a quiet note. 65+: a firmer note, and
in `hold` mode a second tap. There is no green tick and no "all clear", because
a guard that congratulates you for not sending something stupid has become a
slot machine.

## Why a tiered `strength`

`nudge` (the default, and what was asked for) shows what it noticed next to the
draft and never blocks. `hold` marks high risk red and needs a second tap, for
people who want that. `off` stops draft feedback entirely while leaving the
reading intact. The point of shipping all three is that the honest default for a
tool this unusual is the least coercive one.

## What is deliberately absent

- **No sending.** Not a missing feature: there is no code path. The accessibility
  service has no gesture permission and the overlay has no click on the app
  underneath.
- **No auto-insert into the message box.** Suggested text goes to the clipboard.
  Filling the field for someone is one keystroke away from sending for them.
- **No cloud model.** The `ModelConfig` can point anywhere that speaks the OpenAI
  API, but nothing in the app does, and the docs say plainly that the point is
  the conversation staying home.
- **No reading of apps that are not approved.** Enforced twice, in
  `android/.../Apps.kt` and in `harness/secondthought/apps.py`, so a mistake in
  one does not become a leak.
- **No OCR and no screenshots.** Accessibility text is less invasive, cheaper,
  and works on battery. The cost is that it sees only what is rendered, which is
  why every prompt says the transcript is what is on screen.

## Known rough edges

- `kinetic serve` is one model at a time. Running the reader and the guard
  against the same model is fine; against two models means two ports and two
  config entries, which is not built.
- The guard's model call has a 4 s ceiling. On a slow CPU with a slow model, a
  long draft will simply not be reviewed — the rules still run. That is the
  right failure, but it is a failure.
- Per-app accessibility trees drift with every app release. The layout is
  data-driven per app but the *extraction* is generic; a Discord update that
  changes how sender names are exposed will need `ScreenReader.kt` touched.
- The demo fixture is scripted per app, so the demo page shows quality that a
  1.7B model will not reach on the first try. That is why the status pill says
  `demo mode · no model` in orange.
