package dev.veloresearch.secondthought

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject

/**
 * The floating panel.
 *
 * A `TYPE_APPLICATION_OVERLAY` window the person drags where they want it. It is
 * a WebView showing the same HTML the demo page uses, so there is one layout to
 * maintain and the design does not drift between the preview and the phone.
 *
 * Three deliberate limits:
 *
 * - It is not focusable while collapsed, so the keyboard never opens behind it
 *   and the app underneath keeps working normally.
 * - It never sends anything. The panel's only output is the clipboard.
 * - The send button in the app is the app's send button. This overlay has no
 *   access to it and no code that would press it.
 */
class OverlayService : Service() {

    companion object {
        const val ACTION_READING = "dev.veloresearch.secondthought.READING"
        const val ACTION_ERROR = "dev.veloresearch.secondthought.ERROR"
        const val ACTION_HIDE = "dev.veloresearch.secondthought.HIDE"
        const val ACTION_GUARD = "dev.veloresearch.secondthought.GUARD"
        private const val CHANNEL = "overlay"
        private const val NOTIF_ID = 42

        var running = false
            private set

        fun start(context: Context) {
            val i = Intent(context, OverlayService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, OverlayService::class.java))
        }

        fun broadcastReading(json: JSONObject, app: String) {
            instance?.onReading(json, app)
        }

        fun broadcastError(body: String) {
            instance?.post { it.onError(body) }
        }

        fun broadcastHide() {
            instance?.post { it.hidePanel() }
        }

        @Volatile var instance: OverlayService? = null
            private set
    }

    private lateinit var windowManager: WindowManager
    private var root: View? = null
    private var web: WebView? = null
    private var params: WindowManager.LayoutParams? = null
    private lateinit var brain: Brain
    private val main = Handler(Looper.getMainLooper())
    private val bridge = Bridge()

    private var expanded = true
    private var currentApp: String? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        brain = Brain(applicationContext)
        startForeground(NOTIF_ID, notification())
        running = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") {
            stopSelf()
            return START_NOT_STICKY
        }
        if (root == null) addPanel()
        return START_STICKY
    }

    private fun notification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = getSystemService(NotificationManager::class.java)
            if (mgr.getNotificationChannel(CHANNEL) == null) {
                mgr.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Second Thought", NotificationManager.IMPORTANCE_LOW)
                        .apply { setShowBadge(false) }
                )
            }
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val off = PendingIntent.getService(
            this, 1, Intent(this, OverlayService::class.java).setAction("stop"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        return builder
            .setContentTitle("Second Thought is on")
            .setContentText("Reading only the apps you approved")
            .setSmallIcon(android.R.drawable.ic_menu_edit)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Turn off", off).build())
            .setOngoing(true)
            .build()
    }

    private fun addPanel() {
        val view = LayoutInflater.from(this).inflate(R.layout.overlay_panel, null)
        web = view.findViewById(R.id.web)
        web?.settings?.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
        }
        web?.addJavascriptInterface(bridge, "SecondThoughtNative")
        web?.webViewClient = object : WebViewClient() {
            override fun onPageFinished(v: WebView?, url: String?) {
                bridge.ready()
            }
        }
        // The panel is served from the brain's own origin so that its fetch()
        // calls to /read and /guard are same-origin. Nothing is loaded from the
        // network except the brain, over the address the person paired.
        web?.loadUrl(brain.baseUrl + "/?overlay=1" + if (brain.token.isNotEmpty()) "#token" else "")

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE

        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            // NOT_FOCUSABLE is the important flag: the keyboard stays with the
            // chat app underneath, so the overlay never steals input.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 0
            y = 240
        }
        params = lp
        windowManager.addView(view, lp)
        root = view
        drag(view)
    }

    /** Drag from the handle only, so the content stays tappable. */
    private fun drag(view: View) {
        val handle = view.findViewById<View>(R.id.handle)
        var startY = 0
        var touchY = 0f
        handle.setOnTouchListener { _, ev ->
            val lp = params ?: return@setOnTouchListener false
            when (ev.action) {
                MotionEvent.ACTION_DOWN -> {
                    startY = lp.y
                    touchY = ev.rawY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    lp.y = startY + (ev.rawY - touchY).toInt()
                    root?.let { windowManager.updateViewLayout(it, lp) }
                    true
                }
                else -> false
            }
        }
        handle.setOnClickListener { toggleExpanded() }
    }

    private fun toggleExpanded() {
        expanded = !expanded
        post { w ->
            w.evaluateJavascript("window.STOverlay && window.STOverlay.collapse($expanded)", null)
        }
    }

    private fun onReading(json: JSONObject, app: String) {
        post { w ->
            currentApp = app
            showPanel()
            w.evaluateJavascript("window.STOverlay && window.STOverlay.reading(${json})", null)
        }
    }

    private fun onError(body: String) {
        post { w ->
            w.evaluateJavascript("window.STOverlay && window.STOverlay.error(${JSONObject.quote(body)})", null)
        }
    }

    private fun showPanel() {
        root?.visibility = View.VISIBLE
    }

    private fun hidePanel() {
        post { root?.visibility = View.GONE }
    }

    private fun post(fn: (WebView) -> Unit) {
        main.post {
            val w = web ?: return@post
            fn(w)
        }
    }

    override fun onDestroy() {
        instance = null
        running = false
        try {
            root?.let { windowManager.removeView(it) }
        } catch (e: Exception) { /* already gone */ }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** What the WebView can call. The whole native surface, and it is small. */
    inner class Bridge {
        @JavascriptInterface
        fun copy(text: String) {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cm.setPrimaryClip(android.content.ClipData.newPlainText("Second Thought", text))
        }

        @JavascriptInterface
        fun dismiss() {
            main.post { hidePanel() }
        }

        @JavascriptInterface
        fun readAgain() {
            A11yService.instance?.forceRead()
        }

        @JavascriptInterface
        fun baseUrl(): String = brain.baseUrl

        @JavascriptInterface
        fun token(): String = brain.token

        @JavascriptInterface
        fun app(): String = currentApp ?: "generic"

        @JavascriptInterface
        fun ready() {
            // The panel says hello; push whatever reading is current.
            val reading = A11yService.instance?.lastReading
            if (reading != null) {
                main.post { web?.evaluateJavascript("window.STOverlay && window.STOverlay.reading($reading)", null) }
            }
        }

        @JavascriptInterface
        fun guard(draft: String) {
            val screen = A11yService.instance?.let { svc ->
                val pkg = Foreground.app(applicationContext) ?: return
                if (!Apps.isWatchable(pkg)) return
                ScreenReader.read(
                    svc.rootInActiveWindow, pkg,
                    resources.displayMetrics.widthPixels,
                    resources.displayMetrics.heightPixels,
                )
            } ?: return
            screen.put("draft", draft)
            Thread {
                val reply = brain.post("/guard", screen, timeoutMs = 4_000)
                if (reply.code == 200) {
                    post { w -> w.evaluateJavascript("window.STOverlay && window.STOverlay.verdict(${reply.body})", null) }
                }
            }.start()
        }
    }
}
