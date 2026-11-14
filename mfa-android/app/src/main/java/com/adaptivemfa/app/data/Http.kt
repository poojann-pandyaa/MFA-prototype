package com.adaptivemfa.app.data

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class HttpResult(val status: Int, val body: String)

/** The one place that touches the network, so tests can swap in a fake. */
interface Http {
    fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult
}

/**
 * Plain HttpURLConnection plus a tiny cookie jar: the bank keeps its sign-in in a `bank_session` cookie,
 * AMFA uses a bearer token instead and ignores cookies. Cookies live only as long as the process.
 */
class UrlConnectionHttp : Http {
    private val cookies = mutableMapOf<String, String>()

    override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8_000
            // A face check runs detection, liveness and matching on every frame: give it time.
            conn.readTimeout = 60_000
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            synchronized(cookies) {
                if (cookies.isNotEmpty()) conn.setRequestProperty("Cookie", cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
            }
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            // header names are case-insensitive and uvicorn sends them lower-case
            conn.headerFields.forEach { (name, values) -> if (name.equals("Set-Cookie", ignoreCase = true)) values.forEach { remember(it) } }
            val stream = if (status in 200..399) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return HttpResult(status, text)
        } finally {
            conn.disconnect()
        }
    }

    private fun remember(setCookie: String) {
        val pair = setCookie.substringBefore(';')
        val name = pair.substringBefore('=').trim()
        val value = pair.substringAfter('=', "").trim()
        synchronized(cookies) { if (value.isEmpty()) cookies.remove(name) else cookies[name] = value }
    }
}

class ApiException(val status: Int, override val message: String, val code: String? = null) : Exception(message)

/**
 * The bank answers {"detail": "text"} (or a list of validation problems); AMFA answers
 * {"error": {"code": "...", "message": "..."}}.
 */
fun parseError(status: Int, body: String): ApiException = try {
    val json = JSONObject(body)
    val err = json.optJSONObject("error")
    val d: Any? = json.opt("detail")
    when {
        err != null -> ApiException(status, err.optString("message", "Request failed ($status)"), err.optString("code").ifEmpty { null })
        d is String -> ApiException(status, d)
        d is JSONArray -> ApiException(status, (0 until d.length()).joinToString("; ") { d.getJSONObject(it).optString("msg", "invalid") })
        else -> ApiException(status, "Request failed ($status)")
    }
} catch (e: Exception) {
    ApiException(status, "Request failed ($status)")
}
