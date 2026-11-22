package dev.veloresearch.secondthought

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Rect
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Watches the app in front and, when it is one the person approved, reads the
 * conversation and asks the brain what is going on.
 *
 * Flow, and nothing else happens:
 *
 * 1. An accessibility event arrives with a package name.
 * 2. If that package is not on the allowlist, stop. No read, no send.
 * 3. Debounce: a busy chat fires dozens of events a second. One read per
 *    second at most, and only when the text actually changed.
 * 4. Extract the visible nodes, hash them, and skip if nothing changed.
 * 5. POST to /read. Post the result to the overlay.
 *
 * The service never touches the keyboard, never inserts text into another app,
 * and never invokes a click on a send button. The only "action" it can take is
 * to put text on the system clipboard when the person taps copy.
 */
class A11yService : AccessibilityService() {

    companion object {
        @Volatile var instance: A11yService? = null
            private set

        /** Set by OverlayService. The overlay can pause reading without stopping here. */
        @Volatile var readingEnabled: Boolean = true

        @Volatile var lastEventAt: Long = 0
        private const val MIN_INTERVAL_MS = 1_000L
    }

    private val io = Executors.newSingleThreadExecutor()
    private val busy = AtomicBoolean(false)
    private var lastSig = ""
    private var lastRunAt = 0L
    private lateinit var brain: Brain

    /** What the overlay is currently showing, so it can be refreshed in place. */
    @Volatile var lastReading: JSONObject? = null
        private set
    @Volatile var lastApp: String? = null
        private set

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        brain = Brain(applicationContext)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg == applicationContext.packageName) return
        Foreground.note(pkg)
        if (!Apps.isWatchable(pkg)) {
            // A non-approved app is in front: the overlay hides its panel and
            // the last reading is dropped rather than left on screen.
            if (lastApp != null && lastApp != "none") {
                lastApp = "none"
                lastReading = null
                OverlayService.broadcastHide()
            }
            return
        }
        if (!readingEnabled || brain.paused || !brain.enabled) return
        lastApp = Apps.idFor(pkg)

        val now = System.currentTimeMillis()
        lastEventAt = now
        if (now - lastRunAt < MIN_INTERVAL_MS || busy.get()) return
        lastRunAt = now

        val screen = try {
            ScreenReader.read(
                rootInActiveWindow, pkg,
                resources.displayMetrics.widthPixels,
                resources.displayMetrics.heightPixels,
            )
        } catch (e: Exception) {
            null
        } ?: return

        val sig = signature(screen)
        if (sig == lastSig) return
        lastSig = sig
        requestReading(screen)
    }

    fun requestReading(screen: JSONObject) {
        if (!busy.compareAndSet(false, true)) return
        io.execute {
            try {
                val reply = brain.post("/read", screen, timeoutMs = 25_000)
                if (reply.code == 200) {
                    val json = JSONObject(reply.body)
                    lastReading = json
                    OverlayService.broadcastReading(json, lastApp ?: "generic")
                } else {
                    OverlayService.broadcastError(reply.body)
                }
            } catch (e: Exception) {
                OverlayService.broadcastError("""{"error":"${e.message}"}""")
            } finally {
                busy.set(false)
            }
        }
    }

    /** Re-read the current screen even if the signature is unchanged. */
    fun forceRead() {
        lastSig = ""
        lastRunAt = 0
        val pkg = Foreground.app(applicationContext) ?: return
        if (!Apps.isWatchable(pkg)) return
        val screen = ScreenReader.read(
            rootInActiveWindow, pkg,
            resources.displayMetrics.widthPixels,
            resources.displayMetrics.heightPixels,
        ) ?: return
        requestReading(screen)
    }

    private fun signature(screen: JSONObject): String {
        val nodes = screen.optJSONArray("nodes") ?: return ""
        val sb = StringBuilder()
        val n = minOf(nodes.length(), 40)
        // Only the tail matters for "did anything change": chat apps append.
        for (i in maxOf(0, n - 8) until n) {
            sb.append(nodes.getJSONObject(i).optString("text")).append('\u0001')
        }
        return sb.toString() + (screen.optString("draft").hashCode())
    }

    override fun onInterrupt() { /* nothing to interrupt */ }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        io.shutdownNow()
        super.onDestroy()
    }
}
