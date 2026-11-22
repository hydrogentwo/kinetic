package dev.veloresearch.secondthought

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * The link to the brain on the laptop.
 *
 * Two rules, both enforced here rather than trusted to the caller:
 *
 * - Nothing is sent unless the person has turned watching on and the overlay is
 *   not paused. `Brain.enabled` is checked at the top of every call.
 * - The token, when there is one, goes in the header and never in a URL, so it
 *   does not end up in a log or a screenshot.
 *
 * Requests are cheap and short. The reading call is allowed to take the time a
 * local model needs; the guard call has a hard 4 s ceiling, because a nudge that
 * arrives after the message was sent is worse than no nudge.
 */
class Brain(private val context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("brain", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = prefs.getString("url", "http://127.0.0.1:8787") ?: "http://127.0.0.1:8787"
        set(v) = prefs.edit().putString("url", v.trimEnd('/')).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    var enabled: Boolean
        get() = prefs.getBoolean("enabled", false)
        set(v) = prefs.edit().putBoolean("enabled", v).apply()

    /** Set by OverlayService: the harness is running and not paused. */
    @Volatile var paused: Boolean = false

    data class Reply(val code: Int, val body: String)

    fun post(path: String, payload: JSONObject, timeoutMs: Int = 20_000): Reply {
        if (!enabled || paused) return Reply(0, """{"ok":false,"error":"paused"}""")
        val url = URL(baseUrl + path)
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 4_000
            readTimeout = timeoutMs
            doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            if (token.isNotEmpty()) setRequestProperty("Authorization", "Bearer $token")
        }
        return try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            Reply(code, body)
        } catch (e: Exception) {
            Reply(-1, """{"ok":false,"error":"${e.javaClass.simpleName}: ${e.message}"}""")
        } finally {
            conn.disconnect()
        }
    }

    fun get(path: String, timeoutMs: Int = 4_000): Reply {
        val conn = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 2_000
            readTimeout = timeoutMs
            if (token.isNotEmpty()) setRequestProperty("Authorization", "Bearer $token")
        }
        return try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            Reply(code, stream?.bufferedReader()?.use { it.readText() } ?: "")
        } catch (e: Exception) {
            Reply(-1, """{"ok":false,"error":"${e.message}"}""")
        } finally {
            conn.disconnect()
        }
    }

    fun health(): Boolean = try {
        JSONObject(get("/health").body).optBoolean("ok", false)
    } catch (e: Exception) {
        false
    }
}
