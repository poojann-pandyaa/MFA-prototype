package com.demopay.app.data

import com.demopay.app.logic.Signals
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

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
            conn.readTimeout = 20_000
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

class ApiException(val status: Int, override val message: String, val code: String? = null) : Exception(message)

/** FastAPI errors look like {"detail": "text"}, {"detail": {"code","message"}} or a list of validation problems. */
fun parseError(status: Int, body: String): ApiException = try {
    when (val d = JSONObject(body).opt("detail")) {
        is String -> ApiException(status, d)
        is JSONObject -> ApiException(status, d.optString("message", "Request failed"), d.optString("code").ifEmpty { null })
        is JSONArray -> ApiException(status, (0 until d.length()).joinToString("; ") { d.getJSONObject(it).optString("msg", "invalid") })
        else -> ApiException(status, "Request failed ($status)")
    }
} catch (e: Exception) {
    ApiException(status, "Request failed ($status)")
}

data class RiskRequest(
    val payeeVpa: String, val amount: Double, val channel: String = "pay", val requesterIsContact: Boolean = false,
    val attested: Boolean = true, val signals: Signals = Signals(), val baseRate: Double? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("payee_vpa", payeeVpa)
        put("amount", amount)
        put("channel", channel)
        put("requester_is_contact", requesterIsContact)
        put("attested", attested)
        put("signals", signals.toJson())
        baseRate?.let { put("base_rate", it) }
    }
}

class Api(
    private val http: Http,
    private val baseUrl: () -> String,
    private val token: () -> String?,
    private val deviceId: () -> String,
) {
    private fun call(method: String, path: String, body: JSONObject? = null, query: Map<String, String> = emptyMap()): String {
        val q = if (query.isEmpty()) "" else "?" + query.entries.joinToString("&") { "${enc(it.key)}=${enc(it.value)}" }
        val headers = mutableMapOf("Accept" to "application/json", "X-Device-Id" to deviceId())
        token()?.let { headers["Authorization"] = "Bearer $it" }
        if (body != null) headers["Content-Type"] = "application/json"
        val result = try {
            http.request(method, baseUrl().trimEnd('/') + path + q, headers, body?.toString())
        } catch (e: IOException) {
            throw ApiException(0, "Cannot reach the server at ${baseUrl()}. Is it running, and is the address right?")
        }
        if (result.status !in 200..299) throw parseError(result.status, result.body)
        return result.body
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
    private fun obj(method: String, path: String, body: JSONObject? = null, query: Map<String, String> = emptyMap()) = JSONObject(call(method, path, body, query))
    private fun arr(method: String, path: String) = JSONArray(call(method, path))

    fun signup(username: String, password: String): String = obj("POST", "/auth/signup", JSONObject().put("username", username).put("password", password)).getString("token")
    fun login(username: String, password: String): String = obj("POST", "/auth/login", JSONObject().put("username", username).put("password", password)).getString("token")
    fun me(): Me = parseMe(obj("GET", "/me"))
    fun directory(): List<DirectoryItem> = parseDirectory(arr("GET", "/demo/directory"))
    fun personas(): List<Persona> = parsePersonas(arr("GET", "/demo/personas"))
    fun loadPersona(name: String): String = obj("POST", "/demo/persona", JSONObject().put("name", name)).optString("story", "")
    fun reset() { obj("POST", "/demo/reset") }
    fun lookup(vpa: String): Lookup = parseLookup(obj("GET", "/directory/lookup", query = mapOf("vpa" to vpa)))
    fun activity(): List<PaymentRow> = parseRows(arr("GET", "/pay"))
    fun payment(id: Int): Payment = parsePayment(obj("GET", "/pay/$id"))
    fun risk(req: RiskRequest): Payment = parsePayment(obj("POST", "/pay/risk", req.toJson()))
    fun startStep(paymentId: Int, step: Int): Challenge = parseChallenge(obj("POST", "/pay/$paymentId/steps/$step/start"))
    fun answerStep(paymentId: Int, step: Int, body: JSONObject): StepResult = parseStepResult(obj("POST", "/pay/$paymentId/steps/$step/answer", body))
    fun cancel(paymentId: Int): Payment = parsePayment(obj("POST", "/pay/$paymentId/cancel"))
    fun complete(paymentId: Int, pin: String): Double = obj("POST", "/pay/$paymentId/complete", JSONObject().put("pin", pin)).getDouble("balance")
    fun xray(paymentId: Int): XRay = parseXRay(obj("GET", "/demo/xray/$paymentId"))
}
