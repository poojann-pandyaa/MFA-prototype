package com.adaptivemfa.app

import com.adaptivemfa.app.data.Api
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.Step1Result
import com.adaptivemfa.app.data.UrlConnectionHttp
import com.adaptivemfa.app.ui.AppViewModel
import com.adaptivemfa.app.ui.Screen
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.util.UUID

/**
 * A small in-process stand-in for backend/main.py with the same request/response contract and the
 * same rules: a new device is HIGH risk and gets a single-use session bound to user + device; the
 * device is only remembered after a successful step 2; /history needs the bearer token. The face
 * check itself is replaced by "13 JPEG data URLs whose user is the enrolled one".
 */
private class FakeMfaBackend {
    private data class User(val password: String, val face: String, val devices: MutableSet<String> = mutableSetOf(), val history: JSONArray = JSONArray())
    private data class Session(val user: String, val device: String, val challenge: String, var used: Boolean = false)

    private val users = mutableMapOf<String, User>()
    private val sessions = mutableMapOf<String, Session>()
    private val tokens = mutableMapOf<String, String>()
    var faceCheckPasses = true
    private val server: HttpServer = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    val url: String get() = "http://127.0.0.1:${server.address.port}"

    init {
        server.createContext("/") { ex ->
            try {
                handle(ex)
            } catch (e: Exception) {
                ex.reply(500, detail("fake backend error: $e"))
            } finally {
                ex.close()
            }
        }
        server.start()
    }

    fun stop() = server.stop(0)

    private fun HttpExchange.reply(status: Int, body: String) {
        val bytes = body.toByteArray()
        responseHeaders.add("Content-Type", "application/json")
        sendResponseHeaders(status, bytes.size.toLong())
        responseBody.use { it.write(bytes) }
    }

    private fun detail(msg: String) = JSONObject().put("detail", msg).toString()

    private fun token(user: String) = "tok-" + UUID.randomUUID().toString().also { tokens["tok-$it"] = user }

    private fun handle(ex: HttpExchange) {
        val path = ex.requestURI.path
        val body = ex.requestBody.readBytes().toString(Charsets.UTF_8)
        if (ex.requestMethod == "POST" && ex.requestHeaders.getFirst("Content-Type") != "application/json") {
            return ex.reply(422, detail("expected JSON"))
        }
        when ("${ex.requestMethod} $path") {
            "GET /openapi.json" -> ex.reply(200, """{"openapi":"3.1.0"}""")
            "POST /enroll" -> {
                val j = JSONObject(body)
                val name = j.getString("username")
                if (name in users) return ex.reply(400, detail("Username already registered"))
                val face = j.getString("face_image_b64")
                if (!face.startsWith("data:image/jpeg;base64,")) return ex.reply(400, detail("Could not extract face embedding. Ensure a face is clearly visible."))
                users[name] = User(j.getString("password"), face)
                ex.reply(200, JSONObject().put("access_token", token(name)).put("token_type", "bearer").toString())
            }
            "POST /login/step1" -> {
                val j = JSONObject(body)
                val u = users[j.getString("username")]
                if (u == null || u.password != j.getString("password")) return ex.reply(401, detail("Incorrect username or password"))
                val device = j.getString("device_identifier")
                val name = j.getString("username")
                if (device in u.devices) {
                    u.history.put(row(u, device, "LOW", face = false, success = true))
                    ex.reply(200, JSONObject().put("require_step2", false).put("access_token", token(name)).put("token_type", "bearer").put("risk_score", "LOW").toString())
                } else {
                    u.history.put(row(u, device, "HIGH", face = true, success = false))
                    val id = UUID.randomUUID().toString()
                    val challenge = listOf("blink", "turn_left", "turn_right").random()
                    sessions[id] = Session(name, device, challenge)
                    ex.reply(200, JSONObject().put("require_step2", true).put("risk_score", "HIGH").put("session_id", id).put("challenge_type", challenge).toString())
                }
            }
            "POST /login/step2" -> {
                val j = JSONObject(body)
                val images = j.getJSONArray("images")
                if (images.length() > 30) return ex.reply(400, detail("Too many frames submitted."))
                val name = j.getString("username")
                val u = users[name] ?: return ex.reply(400, detail("User not found"))
                val s = sessions[j.getString("session_id")]
                val device = j.getString("device_identifier")
                if (s == null || s.used || s.user != name || s.device != device) return ex.reply(401, detail("Invalid or expired verification session"))
                s.used = true
                val framesOk = images.length() >= 6 && (0 until images.length()).all { images.getString(it).startsWith("data:image/jpeg;base64,") }
                if (!framesOk || !faceCheckPasses) return ex.reply(401, detail("Verification failed. Please try again."))
                u.devices += device
                u.history.getJSONObject(u.history.length() - 1).put("success", true)
                ex.reply(200, JSONObject().put("access_token", token(name)).put("token_type", "bearer").toString())
            }
            "GET /history" -> {
                val auth = ex.requestHeaders.getFirst("Authorization") ?: return ex.reply(401, detail("Not authenticated"))
                val name = tokens[auth.removePrefix("Bearer ")] ?: return ex.reply(401, detail("Could not validate credentials"))
                val h = users.getValue(name).history
                ex.reply(200, JSONArray((h.length() - 1 downTo 0).map { h.get(it) }).toString())
            }
            else -> ex.reply(404, detail("Not Found"))
        }
    }

    private fun row(u: User, device: String, risk: String, face: Boolean, success: Boolean) = JSONObject()
        .put("id", u.history.length() + 1).put("timestamp", "2026-11-14T10:00:00.000000").put("device_identifier", device)
        .put("risk_score", risk).put("face_check_triggered", face).put("success", success)
}

@OptIn(ExperimentalCoroutinesApi::class)
class BackendIntegrationTest {
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var backend: FakeMfaBackend
    private val frames = List(13) { "data:image/jpeg;base64,/9j/AAAA" }

    @Before fun setUp() {
        Dispatchers.setMain(dispatcher)
        backend = FakeMfaBackend()
    }

    @After fun tearDown() {
        backend.stop()
        Dispatchers.resetMain()
    }

    private fun phone(device: String = "android_phone1"): Pair<AppViewModel, InMemorySettings> {
        val s = InMemorySettings(baseUrl = backend.url, deviceId = device)
        return AppViewModel(s, Api(UrlConnectionHttp(), { s.baseUrl }, { s.deviceId }), dispatcher) to s
    }

    @Test fun fullAdaptiveFlowOverRealHttp() {
        val (vm, s) = phone()

        vm.testConnection(backend.url)
        assertEquals("Connected to ${backend.url}", vm.notice)

        vm.enroll("alice", "Demo@12345", "data:image/jpeg;base64,/9j/face")
        assertEquals(Screen.Dashboard, vm.screen)
        vm.logout()

        // First login from this phone: new device, so the face check is required.
        vm.login("alice", "Demo@12345")
        val face = vm.screen as Screen.FaceCheck
        assertEquals("HIGH", face.riskScore)
        assertTrue(face.challengeType in setOf("blink", "turn_left", "turn_right"))

        vm.submitFaceCheck(frames)
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.history.first().success)
        assertTrue(vm.history.first().faceCheckTriggered)
        vm.logout()

        // Second login: the phone is now known, so the password alone is enough.
        vm.login("alice", "Demo@12345")
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.notice!!.contains("LOW"))
        assertEquals(2, vm.history.size)
        assertEquals("LOW", vm.history.first().riskScore)
        assertFalse(vm.history.first().faceCheckTriggered)
        assertTrue(s.token!!.startsWith("tok-"))
    }

    @Test fun anotherPhoneIsAskedForTheFaceAgain() {
        val (first, _) = phone("android_phone1")
        first.enroll("alice", "pw", "data:image/jpeg;base64,/9j/face")
        first.logout()
        first.login("alice", "pw"); first.submitFaceCheck(frames)
        assertEquals(Screen.Dashboard, first.screen)

        val (second, _) = phone("android_phone2")
        second.login("alice", "pw")
        assertTrue(second.screen is Screen.FaceCheck)
    }

    @Test fun failedFaceCheckCannotReuseTheSession() {
        val (vm, _) = phone()
        vm.enroll("alice", "pw", "data:image/jpeg;base64,/9j/face"); vm.logout()
        vm.login("alice", "pw")
        val session = (vm.screen as Screen.FaceCheck).sessionId

        backend.faceCheckPasses = false
        vm.submitFaceCheck(frames)
        assertEquals(Screen.Login, vm.screen)
        assertTrue(vm.error!!.startsWith("Verification failed."))

        // Replaying the used-up session straight through the API is refused even with a passing face.
        backend.faceCheckPasses = true
        val api = Api(UrlConnectionHttp(), { backend.url }, { "android_phone1" })
        try {
            api.loginStep2("alice", session, frames); fail()
        } catch (e: ApiException) {
            assertEquals(401, e.status)
        }
    }

    @Test fun sessionIsBoundToTheDevice() {
        val api1 = Api(UrlConnectionHttp(), { backend.url }, { "android_phone1" })
        val api2 = Api(UrlConnectionHttp(), { backend.url }, { "android_phone2" })
        api1.enroll("alice", "pw", "data:image/jpeg;base64,/9j/face")
        val r = api1.loginStep1("alice", "pw") as Step1Result.FaceCheckRequired
        try {
            api2.loginStep2("alice", r.sessionId, frames); fail()
        } catch (e: ApiException) {
            assertEquals(401, e.status)
        }
    }

    @Test fun serverErrorsReachTheScreen() {
        val (vm, _) = phone()
        vm.login("nobody", "x")
        assertEquals("Incorrect username or password", vm.error)

        vm.enroll("alice", "pw", "data:image/jpeg;base64,/9j/face")
        vm.logout()
        vm.go(Screen.Enroll)
        vm.enroll("alice", "pw", "data:image/jpeg;base64,/9j/face")
        assertEquals("Username already registered", vm.error)
    }

    @Test fun historyNeedsAValidToken() {
        val api = Api(UrlConnectionHttp(), { backend.url }, { "android_phone1" })
        try {
            api.history("tok-forged"); fail()
        } catch (e: ApiException) {
            assertEquals(401, e.status)
        }
    }

    @Test fun stoppedServerGivesTheUnreachableMessage() {
        val port = ServerSocket(0).use { it.localPort }   // a port nothing listens on
        val (vm, _) = phone()
        vm.testConnection("http://127.0.0.1:$port")
        assertTrue(vm.error!!.startsWith("Cannot reach the server at http://127.0.0.1:$port"))
    }
}
