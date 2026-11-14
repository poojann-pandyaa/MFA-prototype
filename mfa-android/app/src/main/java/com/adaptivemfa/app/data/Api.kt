package com.adaptivemfa.app.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

/** What /login/step1 decided. */
sealed interface Step1Result {
    val riskScore: String

    /** Known device at a normal hour: logged in with the password alone. */
    data class LoggedIn(override val riskScore: String, val accessToken: String) : Step1Result

    /** New device or unusual time: a face check is required, with a random gesture. */
    data class FaceCheckRequired(override val riskScore: String, val sessionId: String, val challengeType: String) : Step1Result
}

data class HistoryItem(
    val id: Int,
    val timestamp: String,
    val deviceIdentifier: String,
    val riskScore: String,
    val faceCheckTriggered: Boolean,
    val success: Boolean,
)

fun parseStep1(json: JSONObject): Step1Result {
    val risk = json.optString("risk_score", "UNKNOWN")
    return if (json.optBoolean("require_step2", false)) {
        Step1Result.FaceCheckRequired(risk, json.getString("session_id"), json.getString("challenge_type"))
    } else {
        Step1Result.LoggedIn(risk, json.getString("access_token"))
    }
}

fun parseHistory(arr: JSONArray): List<HistoryItem> = (0 until arr.length()).map { i ->
    val o = arr.getJSONObject(i)
    HistoryItem(
        id = o.getInt("id"),
        timestamp = o.optString("timestamp", ""),
        deviceIdentifier = o.optString("device_identifier", ""),
        riskScore = o.optString("risk_score", ""),
        faceCheckTriggered = o.optBoolean("face_check_triggered", false),
        success = o.optBoolean("success", false),
    )
}

/**
 * Client for the Adaptive MFA FastAPI backend (repo-root `backend/main.py`).
 * Same requests as the web app in `frontend/src/pages`.
 */
class Api(
    private val http: Http,
    private val baseUrl: () -> String,
    private val deviceId: () -> String,
) {
    private fun call(method: String, path: String, body: JSONObject? = null, token: String? = null): String {
        val headers = mutableMapOf("Accept" to "application/json")
        token?.let { headers["Authorization"] = "Bearer $it" }
        if (body != null) headers["Content-Type"] = "application/json"
        val result = try {
            http.request(method, baseUrl().trimEnd('/') + path, headers, body?.toString())
        } catch (e: IOException) {
            throw ApiException(0, "Cannot reach the server at ${baseUrl()}. Is it running, and is the address right (Settings)?")
        }
        if (result.status !in 200..299) throw parseError(result.status, result.body)
        return result.body
    }

    /** Creates the account from a password and one face photo; returns an access token. */
    fun enroll(username: String, password: String, faceImageDataUrl: String): String =
        JSONObject(call("POST", "/enroll", JSONObject()
            .put("username", username)
            .put("password", password)
            .put("face_image_b64", faceImageDataUrl)))
            .getString("access_token")

    fun loginStep1(username: String, password: String): Step1Result =
        parseStep1(JSONObject(call("POST", "/login/step1", JSONObject()
            .put("username", username)
            .put("password", password)
            .put("device_identifier", deviceId()))))

    /** Sends the gesture burst. The session is single use: on any failure, start again at step 1. */
    fun loginStep2(username: String, sessionId: String, frames: List<String>): String =
        JSONObject(call("POST", "/login/step2", JSONObject()
            .put("username", username)
            .put("images", JSONArray(frames))
            .put("device_identifier", deviceId())
            .put("session_id", sessionId)))
            .getString("access_token")

    fun history(token: String): List<HistoryItem> = parseHistory(JSONArray(call("GET", "/history", token = token)))

    /** Cheap reachability check: FastAPI always serves its OpenAPI document. */
    fun ping() {
        call("GET", "/openapi.json")
    }
}
