package dev.veloresearch.secondthought

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import org.json.JSONObject

/**
 * Setup, and the only place the person decides what the harness is allowed to
 * do. Three switches, in the order they have to be turned on:
 *
 * 1. Accessibility - which is what lets it read a screen at all.
 * 2. Overlay - the floating panel.
 * 3. Pairing - the laptop's address and token.
 *
 * Each row says what it does and what it does not do, in place, because a setup
 * screen that hides the trade-off is how people end up with an app reading their
 * banking app.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var brain: Brain
    private lateinit var status: TextView
    private lateinit var a11yState: TextView
    private lateinit var overlayState: TextView
    private lateinit var brainState: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        brain = Brain(applicationContext)
        status = findViewById(R.id.status)
        a11yState = findViewById(R.id.a11y_state)
        overlayState = findViewById(R.id.overlay_state)
        brainState = findViewById(R.id.brain_state)

        findViewById<EditText>(R.id.url).setText(brain.baseUrl)
        findViewById<EditText>(R.id.token).setText(brain.token)

        findViewById<Button>(R.id.a11y_button).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        findViewById<Button>(R.id.usage_button).setOnClickListener {
            UsageStats.openSettings(this)
        }
        findViewById<Button>(R.id.overlay_button).setOnClickListener {
            if (Settings.canDrawOverlays(this)) {
                OverlayService.stop(this)
                OverlayService.start(this)
            } else {
                startActivity(
                    Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
                )
            }
        }
        findViewById<Button>(R.id.save).setOnClickListener {
            val url = findViewById<EditText>(R.id.url).text.toString().trim()
            val token = findViewById<EditText>(R.id.token).text.toString().trim()
            if (!url.startsWith("http")) {
                Toast.makeText(this, "The address needs to start with http://", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            brain.baseUrl = url
            brain.token = token
            Toast.makeText(this, "saved", Toast.LENGTH_SHORT).show()
            refresh()
        }
        findViewById<Button>(R.id.pause).setOnClickListener {
            brain.enabled = !brain.enabled
            if (!brain.enabled) A11yService.readingEnabled = false
            refresh()
        }
        findViewById<Button>(R.id.notifications).setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refresh()
        Thread {
            val ok = brain.health()
            var model = ""
            if (ok) {
                model = runCatching {
                    JSONObject(brain.get("/health").body).optString("backend")
                }.getOrDefault("")
            }
            runOnUiThread {
                brainState.text = when {
                    !ok -> "brain unreachable at ${brain.baseUrl}"
                    model == "fixture" -> "connected, demo mode (no model loaded)"
                    else -> "connected · $model"
                }
                brainState.setTextColor(
                    ContextCompat.getColor(
                        this@MainActivity,
                        if (ok) R.color.ok else R.color.bad,
                    )
                )
            }
        }.start()
    }

    private fun refresh() {
        val a11yOn = A11yService.instance != null
        a11yState.text = if (a11yOn) "on · reading only approved apps" else "off · nothing is being read"
        a11yState.setTextColor(ContextCompat.getColor(this, if (a11yOn) R.color.ok else R.color.bad))

        val overlayOn = Settings.canDrawOverlays(this) && OverlayService.running
        overlayState.text = if (overlayOn) "on · panel visible"
        else if (Settings.canDrawOverlays(this)) "permission granted, panel stopped"
        else "off · no permission"

        status.text = when {
            !brain.enabled -> "Paused. Nothing is read and nothing is sent."
            a11yOn && overlayOn -> "Watching the apps you approved. Nothing leaves this phone except to your own machine."
            else -> "Not watching yet."
        }
        findViewById<Button>(R.id.pause).text = if (brain.enabled) "Pause everything" else "Resume"
    }
}
