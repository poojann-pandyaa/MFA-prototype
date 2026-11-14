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

data class Payee(val ref: String, val display: String, val initials: String)
data class Biller(val ref: String, val display: String)

data class Me(
    val name: String,
    val ref: String,
    val balanceMinor: Long,
    val payees: List<Payee>,
    val recentPayees: List<Payee>,
    val billers: List<Biller>,
    val faceEnrolled: Boolean,
    val voiceEnrolled: Boolean,
    val typingEnrolled: Boolean,
    val authenticatorEnrolled: Boolean,
    val protection: String,
    val have: List<String>,
    val canAdd: List<String>,
    val frozen: Boolean,
    val dailyLimitMinor: Long,
    val sentTodayMinor: Long,
    val codeTo: String,
    /** True on a demo server, where the bank can show the customer the code it was handed instead of sending it. */
    val demoTools: Boolean = false,
)

/** One payment or account change as the bank shows it to the customer (never the risk score or AMFA's decision). */
data class ActivityItem(
    val id: String,
    val kind: String,
    val title: String,
    val amountMinor: Long,
    val status: String,
    val detail: String?,
    val createdAt: Double,
    val payee: String,
    val initials: String,
    val checks: List<String> = emptyList(),
    val reason: String? = null,
    val scamWarning: Boolean = false,
    val reviewAfter: Boolean = false,
    val confirmedWith: List<String> = emptyList(),
    val canContinue: Boolean = false,
    val reported: Boolean = false,
)

/** Where AMFA's own API lives for one approval or one enrolment, and the single-use token for it. */
data class AmfaLink(val base: String, val token: String)

/** The bank's answer to starting a payment: [link] is null when AMFA needs no check for it. */
data class Started(val action: ActivityItem, val link: AmfaLink?) {
    val actionId get() = action.id
}

data class Settled(val action: ActivityItem, val balanceMinor: Long) {
    val status get() = action.status
    val detail get() = action.detail
}

private fun JSONObject.texts(key: String): List<String> =
    optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) } } ?: emptyList()

private fun JSONObject.optNullable(key: String): String? = if (isNull(key)) null else optString(key).ifEmpty { null }

fun parseAction(a: JSONObject): ActivityItem {
    val p = a.optJSONObject("payee")
    return ActivityItem(
        id = a.getString("id"), kind = a.optString("kind"), title = a.optString("title"),
        amountMinor = a.optLong("amountMinor"), status = a.optString("status"), detail = a.optNullable("detail"),
        createdAt = a.optDouble("createdAt", 0.0), payee = p?.optString("display") ?: "", initials = p?.optString("initials") ?: "",
        checks = a.texts("checks"), reason = a.optNullable("reason"), scamWarning = a.optBoolean("scamWarning", false),
        reviewAfter = a.optBoolean("reviewAfter", false), confirmedWith = a.texts("confirmedWith"),
        canContinue = a.optBoolean("canContinue", false), reported = a.optBoolean("reported", false),
    )
}

private fun parsePayees(arr: JSONArray?): List<Payee> =
    arr?.let { (0 until it.length()).map { i -> it.getJSONObject(i).let { p -> Payee(p.getString("ref"), p.optString("display"), p.optString("initials")) } } } ?: emptyList()

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
        val prot = j.optJSONObject("protection")
        val billers = j.optJSONArray("billers")
        return Me(
            name = j.optString("name"), ref = j.optString("ref"), balanceMinor = j.optLong("balance"),
            payees = parsePayees(j.optJSONArray("payees")), recentPayees = parsePayees(j.optJSONArray("recentPayees")),
            billers = billers?.let { (0 until it.length()).map { i -> it.getJSONObject(i).let { b -> Biller(b.getString("ref"), b.optString("display")) } } } ?: emptyList(),
            faceEnrolled = j.optJSONObject("enrolled")?.optString("FACE") == "ENROLLED",
            voiceEnrolled = j.optJSONObject("enrolled")?.optString("VOICE") == "ENROLLED",
            typingEnrolled = j.optJSONObject("enrolled")?.optString("UEBA") == "ENROLLED",
            authenticatorEnrolled = j.optJSONObject("enrolled")?.optBoolean("OTP_TOTP", false) ?: false,
            protection = prot?.optString("level") ?: "", have = prot?.texts("have") ?: emptyList(), canAdd = prot?.texts("canAdd") ?: emptyList(),
            frozen = j.optBoolean("frozen", false), dailyLimitMinor = j.optLong("dailyLimit"), sentTodayMinor = j.optLong("sentToday"),
            codeTo = j.optString("codeTo"), demoTools = j.optBoolean("demoTools", false),
        )
    }

    /** On a demo server: the latest code the bank was handed for this customer, or null if there is none yet. */
    fun demoCode(): String? = call(http, "GET", url("/api/demo/code")).let { if (it.isNull("code")) null else it.optString("code").ifEmpty { null } }

    /** Asks the bank to have AMFA open an enrolment for this customer: "FACE", "VOICE" or "UEBA" (typing). */
    fun startSetup(factor: String): AmfaLink =
        parseAmfaLink(call(http, "POST", url("/api/amfa/enrol"), JSONObject().put("factors", JSONArray().put(factor))).getString("url"))

    fun startFaceSetup(): AmfaLink = startSetup("FACE")

    /** A phone has a camera, a microphone and a touchscreen; it declares them so AMFA can plan face, voice and typing checks. */
    private fun start(kind: String, body: JSONObject): Started {
        val j = call(http, "POST", url("/api/actions/$kind"), body
            .put("deviceId", deviceId()).put("camera", true).put("microphone", true).put("pointer", false).put("touch", true))
        val link = if (j.isNull("challengeUrl")) null else parseAmfaLink(j.getString("challengeUrl"))
        return Started(parseAction(j.getJSONObject("action")), link)
    }

    fun startTransfer(payeeRef: String, amountRupees: Double) =
        start("transfer", JSONObject().put("payeeRef", payeeRef).put("amountRupees", amountRupees))

    fun startBill(billerRef: String, amountRupees: Double) =
        start("bill", JSONObject().put("billerRef", billerRef).put("amountRupees", amountRupees))

    /** The bank asks AMFA how the approval ended, applies the payment only on a verified proof, and says what happened. */
    fun complete(actionId: String): Settled {
        val j = call(http, "POST", url("/api/actions/$actionId/complete"), JSONObject())
        return Settled(parseAction(j.getJSONObject("action")), j.optLong("balance"))
    }

    fun cancel(actionId: String) {
        call(http, "POST", url("/api/actions/$actionId/cancel"), JSONObject())
    }

    fun activity(): List<ActivityItem> {
        val arr = call(http, "GET", url("/api/activity")).optJSONArray("activity") ?: return emptyList()
        return (0 until arr.length()).map { parseAction(arr.getJSONObject(it)) }
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

/** The secret of an authenticator setup, and the otpauth:// link that opens it in an authenticator app. */
data class TotpSetup(val otpauthUri: String, val secret: String)

/** What the customer still has to do for the factor this link asked for; [behaviour] is the typing phrase and trace. */
data class EnrollState(
    val consentRequired: Boolean, val consentVersion: String, val enrolled: Map<String, Boolean>, val behaviour: Behaviour?,
    val otpMethods: List<String> = emptyList(),
) {
    val faceEnrolled get() = enrolled["FACE"] == true
}

/** The typing setup script: type [phrase] [repetitions] times; on a phone also trace [shape] [tracesRequired] times. */
data class Behaviour(val phrase: String, val repetitions: Int, val shape: List<Pair<Double, Double>>, val tracesRequired: Int)

/**
 * One check AMFA asks for. FACE: [action] (TURN_LEFT or TURN_RIGHT), [frames], [intervalMs]. OTP: [mode] ("totp" or
 * "delivered"), [sentTo]. VOICE: [prompt] to read aloud for [minSeconds] to [maxSeconds]. UEBA: [phrase] to type and,
 * when [shape] is given, a line to trace; [input] says which kind of device's template it is compared with.
 */
data class AmfaChallenge(
    val id: String, val factor: String, val action: String?, val frames: Int, val intervalMs: Long, val mode: String?, val sentTo: String?,
    val prompt: String? = null, val minSeconds: Int = 3, val maxSeconds: Int = 10,
    val phrase: String? = null, val shape: List<Pair<Double, Double>>? = null, val input: String? = null,
)

data class SessionState(val status: String, val amount: String, val payee: String, val steps: List<String>, val challenge: AmfaChallenge?)

/** What AMFA said about the attempt itself; [session] is where the approval stands now. */
data class Submitted(val factorStatus: String, val retry: Boolean, val guidance: String?, val session: SessionState)

class AmfaApi(private val http: Http, private val link: AmfaLink) {
    private fun url(path: String) = link.base.trimEnd('/') + "/c/v1" + path

    fun enrollmentState(): EnrollState {
        val j = call(http, "GET", url("/enrollment"), token = link.token)
        val consent = j.optJSONObject("consent")
        val done = j.optJSONObject("enrolled")
        val b = j.optJSONObject("behaviour")
        return EnrollState(consent?.optBoolean("required", true) ?: true, consent?.optString("version") ?: "",
            listOf("FACE", "VOICE", "UEBA", "OTP").associateWith { done?.optBoolean(it, false) ?: false },
            b?.let { Behaviour(it.optString("phrase"), it.optInt("repetitions", 10), parseShape(it.optJSONArray("shape")) ?: emptyList(), it.optInt("shapeTracesRequired", 4)) },
            j.optJSONArray("otpMethods")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList())
    }

    fun consent(version: String) {
        call(http, "POST", url("/enrollment/consent"), JSONObject().put("accepted", true).put("version", version), link.token)
    }

    /** Returns null when the face was enrolled, otherwise AMFA's reason (for example LIVENESS_NOT_ESTABLISHED). */
    fun enrollFace(frames: List<String>): String? {
        val j = call(http, "POST", url("/enrollment/face"), JSONObject().put("frames", JSONArray(frames)), link.token)
        return if (j.optBoolean("enrolled", false)) null else j.optString("reason", "REJECTED")
    }

    /** Voice setup: three clips as base64 WAV. Null when enrolled, otherwise AMFA's reason. */
    fun enrollVoice(clips: List<String>): String? {
        val j = call(http, "POST", url("/enrollment/voice"), JSONObject().put("samples", JSONArray(clips)), link.token)
        return if (j.optBoolean("enrolled", false)) null else j.optString("reason", "REJECTED")
    }

    /** Typing setup: [samples] of key events, plus (on a phone) the traced lines and what drew them. */
    fun enrollBehaviour(samples: List<JSONArray>, traces: List<JSONArray>, input: String): String? {
        val body = JSONObject().put("keystrokeSamples", JSONArray(samples)).put("shapeTraces", JSONArray(traces)).put("input", input)
            .put("shapePointerTypes", JSONArray(traces.map { if (input == "touch") "touch" else "mouse" }))
        val j = call(http, "POST", url("/enrollment/behaviour"), body, link.token)
        return if (j.optBoolean("enrolled", false)) null else j.optString("reason", "REJECTED")
    }

    /** Starts an authenticator-app setup: the secret to type or open in the app. */
    fun totpStart(): TotpSetup {
        val j = call(http, "POST", url("/enrollment/totp/start"), JSONObject(), link.token)
        return TotpSetup(j.getString("otpauthUri"), j.getString("secret"))
    }

    /** Confirms the app by the 6-digit code it shows. */
    fun totpConfirm(code: String) {
        call(http, "POST", url("/enrollment/totp/confirm"), JSONObject().put("code", code), link.token)
    }

    /** Sends a fresh emailed or texted code and returns the session with its new challenge. */
    fun resendCode(): SessionState = parseSession(call(http, "POST", url("/session/otp/resend"), JSONObject(), link.token))

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
                p?.optString("sentTo")?.ifEmpty { null },
                prompt = p?.optString("prompt")?.ifEmpty { null }, minSeconds = p?.optInt("minSeconds", 3) ?: 3, maxSeconds = p?.optInt("maxSeconds", 10) ?: 10,
                phrase = p?.optString("phrase")?.ifEmpty { null }, shape = parseShape(p?.optJSONArray("shape")), input = p?.optString("input")?.ifEmpty { null })
        },
    )
}

private fun parseShape(a: JSONArray?): List<Pair<Double, Double>>? =
    a?.let { arr -> (0 until arr.length()).map { arr.getJSONArray(it).let { pt -> pt.getDouble(0) to pt.getDouble(1) } } }
