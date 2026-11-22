package dev.veloresearch.secondthought

import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.provider.Settings

/**
 * The fallback answer to "which app is in front", via usage stats.
 *
 * Requires the PACKAGE_USAGE_STATS permission, which is granted by hand in
 * Settings. The setup screen checks it and deep-links there. This is the only
 * permission the app needs that is not an ordinary runtime permission, and it is
 * read-only: it says which app is in front, nothing about what is in it.
 */
object UsageStats {

    fun granted(context: Context): Boolean {
        val app = context.applicationContext
        val flat = Settings.Secure.getString(app.contentResolver, "enabled_accessibility_services") ?: ""
        // Accessibility is the other half; both are checked on the setup screen.
        return hasUsage(app) && flat.contains(app.packageName)
    }

    fun hasUsage(context: Context): Boolean = try {
        val manager = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val stats = manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, now - 60_000, now)
        !stats.isNullOrEmpty()
    } catch (e: Exception) {
        false
    }

    /** The most recently used app that is not us and not the launcher. */
    fun foreground(context: Context): String? = try {
        val manager = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val stats = manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, now - 60_000, now)
            ?: return null
        stats.asSequence()
            .filter { it.lastTimeUsed > 0 && it.packageName != context.packageName }
            .filter { it.packageName != "com.android.systemui" }
            .maxByOrNull { it.lastTimeUsed }
            ?.packageName
    } catch (e: Exception) {
        null
    }

    fun openSettings(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            val intent = android.content.Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(intent) }
        }
    }
}
