package com.demopay.app.data

import org.json.JSONArray
import org.json.JSONObject

data class Method(val id: String, val name: String, val tier: String, val answers: String, val seconds: Double, val implemented: Boolean, val enrolled: Boolean)
data class Me(val username: String, val balance: Double, val demo: Boolean, val baseRate: Double, val holdSeconds: Int, val methods: List<Method>)
data class PlanStep(val index: Int, val method: String, val name: String, val tier: String, val why: String, val estSeconds: Double, val doubtAfter: Double)
data class Plan(
    val decision: String, val pScam: Double, val baseRate: Double, val expectedCost: Double, val timeSeconds: Double,
    val notes: List<String>, val steps: List<PlanStep>,
)
data class Payment(
    val id: Int, val status: String, val amount: Double, val payeeVpa: String, val registeredName: String?, val channel: String,
    val severity: String?, val threat: String?, val plan: Plan, val stepsDone: Int, val currentStep: Int?, val message: String,
)
data class Challenge(val nonce: String, val method: String, val data: JSONObject, val attemptsLeft: Int, val expiresAt: String)
data class StepResult(
    val passed: Boolean, val retry: Boolean, val wait: Boolean, val blocked: Boolean, val message: String,
    val attemptsLeft: Int?, val status: String,
)
data class DirectoryItem(val vpa: String, val registeredName: String, val label: String)
data class Persona(val name: String, val story: String, val payments: Int, val balance: Double)
data class PaymentRow(val id: Int, val status: String, val amount: Double, val payeeVpa: String, val createdAt: String)
data class Lookup(val found: Boolean, val registeredName: String?)
data class XRay(
    val severity: String, val threat: String, val model: String, val surprises: Map<String, Double>, val thresholds: Map<String, Double>?,
    val reasons: List<Pair<String, Double>>, val inputs: Map<String, String>, val plan: Plan,
)

// ---- JSON parsing (kept free of Android classes except org.json so it can be unit tested on the JVM) ----------------

private fun JSONObject.str(key: String): String? = if (isNull(key) || !has(key)) null else getString(key)
private fun JSONObject.dbl(key: String, default: Double = 0.0): Double = if (isNull(key) || !has(key)) default else getDouble(key)
private fun JSONObject.intOrNull(key: String): Int? = if (isNull(key) || !has(key)) null else getInt(key)
private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
private fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

fun parsePlan(o: JSONObject): Plan = Plan(
    decision = o.optString("decision", "allow"),
    pScam = o.dbl("p_scam"), baseRate = o.dbl("base_rate"), expectedCost = o.dbl("expected_cost"), timeSeconds = o.dbl("time_seconds"),
    notes = o.optJSONArray("notes")?.strings() ?: emptyList(),
    steps = o.optJSONArray("steps")?.objects()?.map {
        PlanStep(it.getInt("index"), it.getString("method"), it.getString("name"), it.getString("tier"), it.optString("why", ""),
            it.dbl("est_seconds"), it.dbl("doubt_after"))
    } ?: emptyList(),
)

fun parsePayment(o: JSONObject): Payment = Payment(
    id = o.getInt("id"), status = o.getString("status"), amount = o.getDouble("amount"), payeeVpa = o.getString("payee_vpa"),
    registeredName = o.str("registered_name"), channel = o.optString("channel", "pay"), severity = o.str("severity"), threat = o.str("threat"),
    plan = parsePlan(o.optJSONObject("plan") ?: JSONObject()), stepsDone = o.optInt("steps_done", 0), currentStep = o.intOrNull("current_step"),
    message = o.optString("message", ""),
)

fun parseMe(o: JSONObject): Me = Me(
    username = o.getString("username"), balance = o.getDouble("balance"), demo = o.optBoolean("demo", false), baseRate = o.dbl("base_rate"),
    holdSeconds = o.optInt("hold_seconds", 0),
    methods = o.optJSONArray("methods")?.objects()?.map {
        Method(it.getString("id"), it.getString("name"), it.getString("tier"), it.getString("answers"), it.dbl("seconds"),
            it.optBoolean("implemented"), it.optBoolean("enrolled"))
    } ?: emptyList(),
)

fun parseChallenge(o: JSONObject): Challenge = Challenge(
    nonce = o.getString("nonce"), method = o.getString("method"), data = o.getJSONObject("data"), attemptsLeft = o.optInt("attempts_left", 0),
    expiresAt = o.optString("expires_at", ""),
)

fun parseStepResult(o: JSONObject): StepResult = StepResult(
    passed = o.optBoolean("passed"), retry = o.optBoolean("retry"), wait = o.optBoolean("wait"), blocked = o.optBoolean("blocked"),
    message = o.optString("message", ""), attemptsLeft = o.intOrNull("attempts_left"), status = o.optString("status", ""),
)

fun parseDirectory(a: JSONArray): List<DirectoryItem> = a.objects().map { DirectoryItem(it.getString("vpa"), it.optString("registered_name", ""), it.optString("label", "")) }
fun parsePersonas(a: JSONArray): List<Persona> = a.objects().map { Persona(it.getString("name"), it.optString("story", ""), it.optInt("payments"), it.dbl("balance")) }
fun parseRows(a: JSONArray): List<PaymentRow> = a.objects().map { PaymentRow(it.getInt("id"), it.getString("status"), it.getDouble("amount"), it.getString("payee_vpa"), it.optString("created_at", "")) }
fun parseLookup(o: JSONObject): Lookup = Lookup(o.optBoolean("found"), o.str("registered_name"))

fun parseXRay(o: JSONObject): XRay {
    fun map(obj: JSONObject?): Map<String, Double> = obj?.keys()?.asSequence()?.associateWith { obj.getDouble(it) } ?: emptyMap()
    val inputs = o.optJSONObject("inputs")
    return XRay(
        severity = o.optString("severity", "LOW"), threat = o.optString("threat", "NONE"), model = o.optString("model", ""),
        surprises = map(o.optJSONObject("surprises")),
        thresholds = o.optJSONObject("thresholds")?.let { map(it) },
        reasons = o.optJSONArray("reasons")?.objects()?.map { it.getString("input") to it.dbl("pushes_up_by") } ?: emptyList(),
        inputs = inputs?.keys()?.asSequence()?.associateWith { inputs.get(it).toString() } ?: emptyMap(),
        plan = parsePlan(o.optJSONObject("plan") ?: JSONObject()),
    )
}
