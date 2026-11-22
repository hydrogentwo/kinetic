package dev.veloresearch.secondthought

/**
 * The allowlist, in the app.
 *
 * The brain has its own copy with the per-app rules; this one exists so the
 * phone can refuse to read a screen before anything is extracted. A package
 * that is not here is never read, never sent, and never appears in a log.
 *
 * Keep in sync with `harness/secondthought/apps.py`, which `second-thought
 * profile` prints.
 */
object Apps {

    val watchable: Map<String, String> = mapOf(
        "com.whatsapp" to "whatsapp",
        "com.whatsapp.w4b" to "whatsapp",
        "com.discord" to "discord",
        "com.google.android.apps.messaging" to "sms",
        "com.android.mms" to "sms",
        "com.samsung.android.messaging" to "sms",
        "com.textra" to "sms",
        "com.Slack" to "slack",
        "com.SlackEnterprise" to "slack",
        "com.microsoft.teams" to "slack",
        "com.instagram.android" to "instagram",
        "org.telegram.messenger" to "telegram",
        "org.telegram.plus" to "telegram",
        "org.thunderdog.challegram" to "telegram",
        "com.facebook.orca" to "messenger",
        "com.facebook.mlite" to "messenger",
        "org.thoughtcrime.securesms" to "signal",
        "com.linkedin.android" to "linkedin",
    )

    val labels: Map<String, String> = mapOf(
        "whatsapp" to "WhatsApp",
        "discord" to "Discord",
        "sms" to "Messages",
        "slack" to "Slack",
        "instagram" to "Instagram",
        "telegram" to "Telegram",
        "messenger" to "Messenger",
        "signal" to "Signal",
        "linkedin" to "LinkedIn",
    )

    fun idFor(packageName: String): String? = watchable[packageName]

    fun isWatchable(packageName: String): Boolean = watchable.containsKey(packageName)

    fun label(appId: String): String = labels[appId] ?: appId
}
