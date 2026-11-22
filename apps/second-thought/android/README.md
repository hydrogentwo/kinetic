# The Android overlay

A floating panel, a foreground watcher and a setup screen. Nothing else.

## Build

Needs the Android SDK (platform 35, build-tools 35) and JDK 17. Android Studio, or:

```sh
export ANDROID_HOME=$HOME/Android/Sdk
cd apps/second-thought/android
./gradlew installDebug
```

Without a wrapper in the repo, `gradle installDebug` works if you have Gradle
8.9 or newer. minSdk is 29 (Android 10) because both accessibility window
content and `TYPE_APPLICATION_OVERLAY` need it.

## The four switches

The setup screen is the whole permission story, and it is in the order the
permissions actually have to be granted.

1. **Accessibility.** `BIND_ACCESSIBILITY_SERVICE`, with
   `canRetrieveWindowContent` and `packageNames` set to the allowlist. There is
   deliberately no `canPerformGestures` and no key-event filtering: the service
   cannot click, swipe, or see a keyboard it does not own.
2. **Usage access.** `PACKAGE_USAGE_STATS`. Without it the app has to guess
   which window is in front, and guessing wrong means reading the wrong one.
   With it, `Foreground.app()` is exact.
3. **Overlay.** `SYSTEM_ALERT_WINDOW`, for the panel.
4. **Pairing.** The laptop's `http://ip:8787` and the token that
   `second-thought serve --lan` printed.

## What each file is

| File | Job |
|---|---|
| `A11yService.kt` | on an event: is the package approved? debounce, extract, hash, POST |
| `ScreenReader.kt` | accessibility tree → the node list the brain parses |
| `Apps.kt` | the allowlist, on the phone. A package not here is never read |
| `Brain.kt` | HTTP to the laptop. `enabled` and `paused` are checked on every call |
| `OverlayService.kt` | the floating window, and the four things JS can call |
| `UsageStats.kt` | which app is in front, as a fallback |
| `MainActivity.kt` | setup, and the pause button |

## The panel is a WebView, on purpose

`OverlayService` loads `http://<laptop>:8787/?overlay=1` into a
`TYPE_APPLICATION_OVERLAY` window. That means:

- one layout for the browser demo and the phone, so they cannot drift;
- the panel's `fetch('/read')` is same-origin with the brain, so no CORS
  trouble and no token in a URL;
- the panel is updated by the laptop, not bundled, so a change to how a nudge
  reads does not need a new APK.

The cost is that the phone needs the laptop reachable to show a panel at all.
That is the trade this makes: no panel without the brain, because a panel with
no brain could only guess.

## Why `FLAG_NOT_FOCUSABLE`

Without it the overlay would take input focus, the keyboard would open into the
panel instead of the chat app, and the person could not type the message they
were about to be talked out of. With it, the app underneath behaves normally and
the panel only ever displays. The handle is the one draggable part.

## The one thing to check on a real device

Accessibility trees differ per app and per version. If a thread reads wrong,
the fastest fix is to dump it:

```sh
adb logcat -s SecondThought
```

and compare the node list with what the brain makes of it:

```sh
python3 -m secondthought read dump.json
```

`read` prints the exact transcript the model sees. If the transcript is right and
the suggestion is wrong, it is a prompt problem in `reading.py`. If the
transcript is wrong, it is `ScreenReader.kt` — usually a sender name that arrives
as its own node, or a message that arrives split across two.

## Tests

There are none here that can run without a device, and pretending otherwise
would be worse than saying so. The logic worth testing lives in the brain, where
`python3 -m secondthought check` covers it with no phone involved. What a device
adds is the trees, and those are checked by reading a real thread and comparing
the transcript.
