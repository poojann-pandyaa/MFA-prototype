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

class UrlConnectionHttp : Http {
    override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8_000
            // Step 2 runs face detection, anti-spoofing and face matching on every frame: give it time.
            conn.readTimeout = 60_000
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            val stream = if (status in 200..399) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return HttpResult(status, text)
        } finally {
            conn.disconnect()
        }
    }
}

class ApiException(val status: Int, override val message: String) : Exception(message)

/** FastAPI errors look like {"detail": "text"} or {"detail": [validation problems]}. */
fun parseError(status: Int, body: String): ApiException = try {
    when (val d = JSONObject(body).opt("detail")) {
        is String -> ApiException(status, d)
        is JSONArray -> ApiException(status, (0 until d.length()).joinToString("; ") { d.getJSONObject(it).optString("msg", "invalid") })
        else -> ApiException(status, "Request failed ($status)")
    }
} catch (e: Exception) {
    ApiException(status, "Request failed ($status)")
}
