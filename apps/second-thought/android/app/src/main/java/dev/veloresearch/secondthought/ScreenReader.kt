package dev.veloresearch.secondthought

import android.content.Context
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Turns the accessibility tree of whatever app is on screen into the node list
 * the brain parses.
 *
 * What it does NOT do, by design:
 *
 * - It does not read an app that is not on the allowlist. The package name is
 *   checked before a single node is touched, so a banking app is not merely
 *   ignored downstream, it is never read.
 * - It does not read password fields, or anything inside a node marked
 *   password, or the contents of a WebView that has opted out.
 * - It does not read anything when the overlay is paused.
 *
 * Accessibility only exposes what is on screen and rendered: scrolling back
 * through history means the harness reads what it can see, which is why the
 * brain says so in every prompt instead of pretending it has the whole thread.
 */
object ScreenReader {

    private const val MAX_NODES = 400
    private const val MAX_TEXT = 4000

    /** Row shape the brain understands. Keep in sync with `transcript.parse`. */
    fun read(
        root: AccessibilityNodeInfo?,
        packageName: String?,
        screenWidth: Int,
        screenHeight: Int,
        includeEditable: Boolean = true,
    ): JSONObject? {
        if (root == null || packageName == null) return null
        if (!Apps.isWatchable(packageName)) return null

        val nodes = JSONArray()
        var visited = 0
        var editable: String? = null

        // Depth-first, in tree order, which is the order a human reads the
        // messages in for every chat app checked so far.
        fun walk(node: AccessibilityNodeInfo?, depth: Int) {
            if (node == null || visited >= MAX_NODES || depth > 40) return
            visited++
            try {
                val text = node.text?.toString()?.trim()
                    ?: node.contentDescription?.toString()?.trim()
                val isEditable = node.isEditable
                if (!text.isNullOrEmpty()) {
                    if (isEditable) {
                        if (includeEditable && editable == null) editable = text
                    } else if (!node.isPassword && text.length <= MAX_TEXT) {
                        val rect = android.graphics.Rect()
                        node.getBoundsInScreen(rect)
                        val onScreen = rect.bottom > 0 && rect.top < screenHeight
                        if (onScreen) {
                            nodes.put(JSONObject().apply {
                                put("text", text)
                                put("bounds", JSONArray(listOf(rect.left, rect.top, rect.right, rect.bottom)))
                                if (!node.isClickable && text.length < 48) {
                                    // A bare label is very often a sender name; the
                                    // parser decides, this just says it might be.
                                    put("label_like", true)
                                }
                            })
                        }
                    }
                }
                for (i in 0 until node.childCount) {
                    walk(node.getChild(i), depth + 1)
                }
            } finally {
                // Nodes obtained from getChild must be recycled on older APIs;
                // on modern Android this is a no-op but harmless.
                @Suppress("DEPRECATION")
                if (node !== root) node.recycle()
            }
        }

        walk(root, 0)
        if (nodes.length() == 0 && editable == null) return null

        return JSONObject().apply {
            put("package", packageName)
            put("app", Apps.idFor(packageName) ?: "generic")
            put("nodes", nodes)
            put("width", screenWidth)
            put("height", screenHeight)
            put("self_names", JSONArray(listOf("You", "Me")))
            if (editable != null) put("draft", editable)
        }
    }

    /** The app the person is in right now, or null if it is not one we read. */
    fun foregroundApp(context: Context): String? {
        val pkg = Foreground.app(context) ?: return null
        return Apps.idFor(pkg)
    }
}

/**
 * Which app is in front.
 *
 * There is no clean API for this, and pretending otherwise is how these apps
 * end up reading the wrong window. Accessibility events are used when they
 * arrive (they carry the package), and the usage-stats permission is the
 * fallback, which is why the app asks for it on the setup screen. If neither is
 * available the overlay simply does not read, and says so.
 */
object Foreground {
    @Volatile private var last: String? = null
    @Volatile private var at: Long = 0

    fun note(packageName: String?) {
        if (packageName != null && packageName != "dev.veloresearch.secondthought") {
            last = packageName
            at = System.currentTimeMillis()
        }
    }

    fun app(context: Context): String? {
        val cached = last
        if (cached != null && System.currentTimeMillis() - at < 10_000) return cached
        val usage = UsageStats.foreground(context)
        if (usage != null) {
            last = usage
            at = System.currentTimeMillis()
        }
        return last
    }
}
