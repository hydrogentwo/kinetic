"""What each app means.

A conversation is not just text. The same sentence is a friendly check-in in a
WhatsApp group and a performance review in Slack. The reading half is told which
app it is looking at, what that app's register is, and who else is in the room,
so that a suggestion is one the person could actually send there.

Each entry is data, not code, so adding an app is one dict.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppProfile:
    id: str
    label: str
    android_packages: tuple[str, ...]
    register: str
    conventions: str
    # How much a single unanswered message stings in this app. The guard uses it
    # to decide whether to warn about a follow-up.
    followup_weight: str = "normal"  # "normal" | "high" | "low"
    # What the overlay should say when it has nothing better to say.
    empty_hint: str = "Nothing needed from you yet."


APPS: dict[str, AppProfile] = {
    "whatsapp": AppProfile(
        id="whatsapp",
        label="WhatsApp",
        android_packages=("com.whatsapp", "com.whatsapp.w4b"),
        register="personal, conversational, often family",
        conventions=(
            "Chats are with people you know. Fragments and real sentences both read as normal. "
            "Blue ticks make silence visible, which is why a follow-up can feel heavier here than "
            "in other apps. Voice notes exist: 'call me' or 'voice note?' is a normal offer. "
            "Group chats usually have a subject and one or two people doing most of the talking."
        ),
        followup_weight="high",
        empty_hint="They read it. Waiting is a reply too.",
    ),
    "discord": AppProfile(
        id="discord",
        label="Discord",
        android_packages=("com.discord",),
        register="lowercase, fast, in-jokes, fragments",
        conventions=(
            "Two very different places: DMs, and servers where many people are present and the "
            "conversation is public to that server. In a server, do not write as if it is private "
            "and do not suggest moving to DMs unless the person asks. Never suggest @everyone or "
            "@here. Custom emoji (:name:) and replies-as-quotes are part of the texture, so a "
            "suggestion may use them if the thread already does. Lowercase and missing punctuation "
            "are normal and are not sloppiness."
        ),
        followup_weight="normal",
        empty_hint="The channel is moving. Nothing from you is overdue.",
    ),
    "sms": AppProfile(
        id="sms",
        label="Messages",
        android_packages=(
            "com.google.android.apps.messaging",
            "com.android.mms",
            "com.samsung.android.messaging",
            "com.textra",
        ),
        register="short, personal, no formatting",
        conventions=(
            "SMS with people you know, on a phone that may not render links or formatting well. "
            "Keep it to one or two short sentences. A long message here is an escalation. "
            "Some contacts are not smartphone users: no reactions, no GIFs, no read receipts."
        ),
        followup_weight="high",
        empty_hint="Nothing to add. One message is usually enough here.",
    ),
    "slack": AppProfile(
        id="slack",
        label="Slack",
        android_packages=("com.Slack", "com.SlackEnterprise", "com.microsoft.teams"),
        register="workplace, concise, threaded",
        conventions=(
            "Work. Replies belong in the thread they answer, not in the channel. Other people's "
            "managers may read it. Brevity is respected and so is not replying. Avoid jokes that "
            "need tone of voice to land, avoid emoji beyond what the channel already uses, and never "
            "commit to a date or scope on someone's behalf. If a question is ambiguous, asking one "
            "short clarifying question is a good reply."
        ),
        followup_weight="low",
        empty_hint="Nothing is blocked on you here.",
    ),
    "instagram": AppProfile(
        id="instagram",
        label="Instagram",
        android_packages=("com.instagram.android",),
        register="very short, informal, reactions",
        conventions=(
            "DMs are short and emoji are normal here in a way that would read as strange in SMS. "
            "Links are suppressed in DMs, so do not suggest sending one. There are usually other "
            "threads open with the same person (reels, story replies) but you can only see this one."
        ),
        followup_weight="high",
        empty_hint="Nothing needed. Let the story reply stand.",
    ),
    "telegram": AppProfile(
        id="telegram",
        label="Telegram",
        android_packages=("org.telegram.messenger", "org.telegram.plus", "org.thunderdog.challegram"),
        register="direct, mixed groups",
        conventions=(
            "DMs and channels and groups that can be very large. Assume bystanders in any group. "
            "Some chats are anonymous channels where you cannot reply at all: if the transcript "
            "shows a channel post, the honest answer is that there is nothing to send."
        ),
        followup_weight="normal",
        empty_hint="Channel posts take no reply.",
    ),
    "messenger": AppProfile(
        id="messenger",
        label="Messenger",
        android_packages=("com.facebook.orca", "com.facebook.mlite"),
        register="casual, friends and family",
        conventions=(
            "Casual chat with people you know. Reactions and short replies are the normal unit. "
            "Group chats are common and are often just shared links and reactions."
        ),
        followup_weight="normal",
    ),
    "signal": AppProfile(
        id="signal",
        label="Signal",
        android_packages=("org.thoughtcrime.securesms",),
        register="private, personal",
        conventions=(
            "Private messaging with people who chose it for privacy. Do not suggest forwarding, "
            "screenshots or anything that moves the conversation out of the app."
        ),
        followup_weight="high",
    ),
    "linkedin": AppProfile(
        id="linkedin",
        label="LinkedIn",
        android_packages=("com.linkedin.android",),
        register="professional, formal, potentially public",
        conventions=(
            "Professional. Every message is written as if it could be forwarded. No emoji, no "
            "sarcasm, no in-jokes. Full sentences, one clear ask, and never fabricate shared "
            "history ('great meeting you') that the transcript does not show."
        ),
        followup_weight="low",
    ),
    "generic": AppProfile(
        id="generic",
        label="this app",
        android_packages=(),
        register="unknown",
        conventions=(
            "The app is not one the harness has a profile for, so assume nothing about its norms. "
            "Match the length and formality of the transcript and do not add formatting the "
            "transcript does not already use."
        ),
        followup_weight="normal",
    ),
}


PACKAGE_TO_APP: dict[str, str] = {
    pkg: app.id for app in APPS.values() for pkg in app.android_packages
}


def profile(app_id: str | None) -> AppProfile:
    if not app_id:
        return APPS["generic"]
    return APPS.get(app_id.strip().lower(), APPS["generic"])


def for_package(package: str | None) -> AppProfile | None:
    """The profile for an Android package, or None if it is not watchable.

    None is the answer the Android app acts on: it stops reading the screen.
    """
    if not package:
        return None
    app_id = PACKAGE_TO_APP.get(package)
    return APPS[app_id] if app_id else None


def allowlist(apps: list[str] | None) -> list[str]:
    """Package names the harness is configured to read."""
    wanted = apps if apps is not None else [a for a in APPS if a != "generic"]
    out: list[str] = []
    for app_id in wanted:
        out.extend(profile(app_id).android_packages)
    return sorted(set(out))
