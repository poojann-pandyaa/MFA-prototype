package com.adaptivemfa.app.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

// ---------------------------------------------------------------- shared plumbing

private fun call(http: Http, method: String, url: String, body: JSONObject? = null, token: String? = null): JSONObject {
    val headers = mutableMapOf("Accept" to "application/json")
    token?.let { headers["Authorization"] = "Bearer $it" }
    if (body != null) headers["Content-Type"] = "application/json"
    val result = try {
        http.request(method, url, headers, body?.toString())
    } catch (e: IOException) {
        throw ApiException(0, "Cannot reach ${url.substringAfter("://").substringBefore('/')}. Is it running, and is the address right (Settings)?")
    }
    if (result.status !in 200..299) throw parseError(result.status, result.body)
    return if (result.body.isBlank()) JSONObject() else JSONObject(result.body)
}

// ---------------------------------------------------------------- bank (the demo bank's own JSON API)

data class Payee(val ref: String, val display: String)

data class Me(
    val name: String,
    val balanceMinor: Long,
    val payees: List<Payee>,
    val faceEnrolled: Boolean,
    val protection: String,
    val frozen: Boolean,
)

data class ActivityItem(val id: String, val title: String, val amountMinor: Long, val status: String, val detail: String?, val payee: String)

/** Where AMFA's own API lives for one approval or one enrolment, and the single-use token for it. */
data class AmfaLink(val base: String, val token: String)

/** The bank's [startTransfer] answer: [link] is null when AMFA needs no check for this payment. */
data class Started(val actionId: String, val status: String, val link: AmfaLink?)

data class Settled(val status: String, val detail: String?, val balanceMinor: Long)

class BankApi(
    private val http: Http,
    private val baseUrl: () -> String,
    private val deviceId: () -> String,
) {
    private fun url(path: String) = baseUrl().trimEnd('/') + path

    fun register(username: String, name: String, phone: String, email: String, password: String) {
        call(http, "POST", url("/api/register"), JSONObject()
            .put("username", username).put("name", name).put("phone", phone)
            .put("email", email).put("password", password).put("deviceId", deviceId()))
    }

    fun login(username: String, password: String) {
        call(http, "POST", url("/api/login"), JSONObject().put("username", username).put("password", password))
    }

    fun logout() {
        call(http, "POST", url("/api/logout"), JSONObject())
    }

    /** Cheap reachability check: the bank serves its page config to anyone. */
    fun ping() {
        call(http, "GET", url("/assets/config.json"))
    }

    fun me(): Me {
        val j = call(http, "GET", url("/api/me?deviceId=" + deviceId()))
        val payees = j.optJSONArray("payees") ?: JSONArray()
        return Me(
            name = j.optString("name"),
            balanceMinor = j.optLong("balance"),
            payees = (0 until payees.length()).map { payees.getJSONObject(it).let { p -> Payee(p.getString("ref"), p.optString("display")) } },
            faceEnrolled = j.optJSONObject("enrolled")?.optString("FACE") == "ENROLLED",
            protection = j.optJSONObject("protection")?.optString("level") ?: "",
            frozen = j.optBoolean("frozen", false),
        )
    }

    /** Asks the bank to have AMFA open a face enrolment for this customer. */
    fun startFaceSetup(): AmfaLink =
        parseAmfaLink(call(http, "POST", url("/api/amfa/enrol"), JSONObject().put("factors", JSONArray().put("FACE"))).getString("url"))

    /** The phone cannot show a microphone or a keyboard-typing check, so it says so; AMFA plans around that. */
    fun startTransfer(payeeRef: String, amountRupees: Double): Started {
        val j = call(http, "POST", url("/api/actions/transfer"), JSONObject()
            .put("payeeRef", payeeRef).put("amountRupees", amountRupees).put("deviceId", deviceId())
            .put("camera", true).put("microphone", false).put("pointer", false).put("touch", true))
        val action = j.getJSONObject("action")
        val link = if (j.isNull("challengeUrl")) null else parseAmfaLink(j.getString("challengeUrl"))
        return Started(action.getString("id"), action.optString("status"), link)
    }

    /** The bank asks AMFA how the approval ended, applies the payment only on a verified proof, and says what happened. */
    fun complete(actionId: String): Settled {
        val j = call(http, "POST", url("/api/actions/$actionId/complete"), JSONObject())
        val a = j.getJSONObject("action")
        return Settled(a.optString("status"), a.optString("detail").ifEmpty { null }, j.optLong("balance"))
    }

    fun cancel(actionId: String) {
        call(http, "POST", url("/api/actions/$actionId/cancel"), JSONObject())
    }

    fun activity(): List<ActivityItem> {
        val arr = call(http, "GET", url("/api/activity")).optJSONArray("activity") ?: return emptyList()
        return (0 until arr.length()).map {
            val a = arr.getJSONObject(it)
            ActivityItem(a.getString("id"), a.optString("title"), a.optLong("amountMinor"), a.optString("status"),
                a.optString("detail").ifEmpty { null }, a.optJSONObject("payee")?.optString("display") ?: "")
        }
    }
}

/** `http://host:8040/hosted/challenge?t=demo-bank&embed=1#token=abc` -> base `http://host:8040`, token `abc`. */
fun parseAmfaLink(url: String): AmfaLink {
    val token = url.substringAfter('#', "").split('&').firstOrNull { it.startsWith("token=") }?.removePrefix("token=")
        ?: throw ApiException(0, "The bank sent a verification link without a token.")
    val scheme = url.substringBefore("://", "")
    val authority = url.substringAfter("://").substringBefore('/').substringBefore('?').substringBefore('#')
    if (scheme.isEmpty() || authority.isEmpty()) throw ApiException(0, "The bank sent a malformed verification link.")
    return AmfaLink("$scheme://$authority", token)
}

// ---------------------------------------------------------------- AMFA customer API (/c/v1)

data class EnrollState(val consentRequired: Boolean, val consentVersion: String, val faceEnrolled: Boolean)

/** [action] is TURN_LEFT or TURN_RIGHT for a face check; [mode] is "totp" or "delivered" for a code; [sentTo] masks where it went. */
data class AmfaChallenge(val id: String, val factor: String, val action: String?, val frames: Int, val intervalMs: Long, val mode: String?, val sentTo: String?)

data class SessionState(val status: String, val amount: String, val payee: String, val steps: List<String>, val challenge: AmfaChallenge?)

/** What AMFA said about the attempt itself; [session] is where the approval stands now. */
data class Submitted(val factorStatus: String, val retry: Boolean, val guidance: String?, val session: SessionState)

class AmfaApi(private val http: Http, private val link: AmfaLink) {
    private fun url(path: String) = link.base.trimEnd('/') + "/c/v1" + path

    fun enrollmentState(): EnrollState {
        val j = call(http, "GET", url("/enrollment"), token = link.token)
        val consent = j.optJSONObject("consent")
        return EnrollState(consent?.optBoolean("required", true) ?: true, consent?.optString("version") ?: "",
            j.optJSONObject("enrolled")?.optBoolean("FACE", false) ?: false)
    }

    fun consent(version: String) {
        call(http, "POST", url("/enrollment/consent"), JSONObject().put("accepted", true).put("version", version), link.token)
    }

    /** Returns null when the face was enrolled, otherwise AMFA's reason (for example LIVENESS_NOT_ESTABLISHED). */
    fun enrollFace(frames: List<String>): String? {
        val j = call(http, "POST", url("/enrollment/face"), JSONObject().put("frames", JSONArray(frames)), link.token)
        return if (j.optBoolean("enrolled", false)) null else j.optString("reason", "REJECTED")
    }

    fun enrollComplete() {
        call(http, "POST", url("/enrollment/complete"), JSONObject(), link.token)
    }

    fun session(): SessionState = parseSession(call(http, "GET", url("/session"), token = link.token))

    fun submit(factor: String, challengeId: String, payload: JSONObject): Submitted {
        val j = call(http, "POST", url("/session/factors/$factor"),
            JSONObject().put("challengeId", challengeId).put("payload", payload), link.token)
        val r = j.optJSONObject("result")
        return Submitted(r?.optString("status") ?: "", r?.optBoolean("retry", false) ?: false,
            r?.optString("guidance")?.ifEmpty { null }, parseSession(j.getJSONObject("state")))
    }
}

fun parseSession(j: JSONObject): SessionState {
    val approving = j.optJSONObject("approving")
    val steps = j.optJSONArray("steps")
    val ch = j.optJSONObject("challenge")
    val p = ch?.optJSONObject("params")
    return SessionState(
        status = j.optString("status"),
        amount = approving?.optString("amount") ?: "",
        payee = approving?.optString("payee") ?: "",
        steps = steps?.let { a -> (0 until a.length()).map { a.getJSONObject(it).optString("factor") } } ?: emptyList(),
        challenge = ch?.let {
            AmfaChallenge(it.getString("challengeId"), it.optString("factor"), p?.optString("action")?.ifEmpty { null },
                p?.optInt("frames", 20) ?: 20, p?.optLong("intervalMs", 150) ?: 150, p?.optString("mode")?.ifEmpty { null },
                p?.optString("sentTo")?.ifEmpty { null })
        },
    )
}
