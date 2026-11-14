package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.BankApi
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
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress

/**
 * One in-process server playing both the demo bank (cookie sign-in, /api/...) and AMFA (bearer token,
 * /c/v1/...), so the real network class is exercised: the bank's session cookie has to be sent back, and
 * AMFA's single-use token has to reach AMFA only. The face check itself is replaced by
 * "20 frames, and the challenge id the session issued".
 */
private class FakeBankAndAmfa {
    private val server: HttpServer = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    val url: String get() = "http://127.0.0.1:${server.address.port}"
    var sessionStatus = "PENDING"
    var paid = false
    var rejectedWithoutCookie = 0
    var rejectedWithoutToken = 0
    var faceFramesSeen = 0

    init {
        server.createContext("/") { ex ->
            try { handle(ex) } catch (e: Exception) { send(ex, 500, """{"detail":"${e.message}"}""") }
        }
        server.start()
    }

    fun stop() = server.stop(0)

    private fun send(ex: HttpExchange, status: Int, body: String, headers: Map<String, String> = emptyMap()) {
        val bytes = body.toByteArray()
        headers.forEach { (k, v) -> ex.responseHeaders.add(k, v) }
        ex.responseHeaders.add("Content-Type", "application/json")
        ex.sendResponseHeaders(status, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    private fun handle(ex: HttpExchange) {
        val path = ex.requestURI.path
        val body = ex.requestBody.readBytes().toString(Charsets.UTF_8)
        val signedIn = ex.requestHeaders.getFirst("Cookie")?.contains("bank_session=alice-cookie") == true
        val bearer = ex.requestHeaders.getFirst("Authorization")
        fun bank(block: () -> String) =
            if (signedIn) send(ex, 200, block()) else { rejectedWithoutCookie++; send(ex, 401, """{"detail":"sign in to the bank first"}""") }
        fun amfa(block: () -> String) =
            if (bearer == "Bearer sess-tok") send(ex, 200, block()) else { rejectedWithoutToken++; send(ex, 401, """{"error":{"code":"TOKEN_INVALID","message":"token invalid or expired"}}""") }

        when ("${ex.requestMethod} $path") {
            "POST /api/login" -> send(ex, 200, """{"signedIn":true}""", mapOf("Set-Cookie" to "bank_session=alice-cookie; HttpOnly; Path=/; SameSite=strict"))
            "GET /api/me" -> bank { """{"name":"Alice","balance":50000000,"enrolled":{"FACE":"ENROLLED"},"protection":{"level":"Good"},"payees":[{"ref":"BEN-42","display":"V. Mehta"}]}""" }
            "GET /api/activity" -> bank { """{"activity":[],"balance":50000000}""" }
            "POST /api/actions/transfer" -> bank {
                """{"action":{"id":"ACT1","kind":"transfer","status":"AWAITING_VERIFICATION","amountMinor":500000,"checks":["face"],"payee":{"display":"V. Mehta"}},"challengeUrl":"http://localhost:1/hosted/challenge?t=demo#token=sess-tok"}"""
                    .replace("http://localhost:1", url)
            }
            "GET /c/v1/session" -> amfa { session() }
            "POST /c/v1/session/factors/FACE" -> amfa {
                val j = JSONObject(body)
                faceFramesSeen = j.getJSONObject("payload").getJSONArray("frames").length()
                if (j.getString("challengeId") == "ch_aaaaaaaaaaaaaaaaaaaa" && faceFramesSeen == 20) sessionStatus = "VERIFIED"
                """{"result":{"factor":"FACE","status":"${if (sessionStatus == "VERIFIED") "PASSED" else "FAILED"}","retry":false,"guidance":null},"escalation":null,"state":${session()}}"""
            }
            "POST /api/actions/ACT1/complete" -> bank {
                if (sessionStatus == "VERIFIED") paid = true
                """{"action":{"id":"ACT1","status":"${if (paid) "DONE" else "AWAITING_VERIFICATION"}","detail":null},"balance":49500000}"""
            }
            else -> send(ex, 404, """{"detail":"no such route $path"}""")
        }
    }

    private fun session() = """{"status":"$sessionStatus","approving":{"amount":"₹5,000.00","payee":"V. Mehta"},"steps":[{"factor":"FACE","status":"PENDING"}],""" +
        """"challenge":${if (sessionStatus == "PENDING") """{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"FACE","params":{"action":"TURN_RIGHT","frames":20,"intervalMs":150}}""" else "null"}}"""
}

@OptIn(ExperimentalCoroutinesApi::class)
class BackendIntegrationTest {
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var fake: FakeBankAndAmfa

    @Before fun setUp() { Dispatchers.setMain(dispatcher); fake = FakeBankAndAmfa() }
    @After fun tearDown() { Dispatchers.resetMain(); fake.stop() }

    private fun vm(settings: InMemorySettings = InMemorySettings(baseUrl = fake.url)): AppViewModel {
        val http = UrlConnectionHttp()
        return AppViewModel(settings, BankApi(http, { settings.baseUrl }, { settings.deviceId }), { AmfaApi(http, it) }, dispatcher)
    }

    @Test fun signInPayWithAFaceCheckAndTheBankSettlesItOverRealHttp() {
        val vm = vm()
        vm.signIn("alice", "pw")
        assertEquals(Screen.Home, vm.screen)
        assertEquals(0, fake.rejectedWithoutCookie)   // the session cookie was sent back on /api/me and /api/activity

        vm.startPayment("transfer", "BEN-42", 5000.0)
        assertTrue(vm.screen is Screen.Review)
        vm.confirmPayment()
        val v = vm.screen as Screen.Verify
        assertEquals("TURN_RIGHT", v.session.challenge!!.action)

        vm.submitFace(List(20) { "data:image/jpeg;base64,AAAA" })
        assertEquals(20, fake.faceFramesSeen)
        assertTrue(fake.paid)
        assertEquals("ok", (vm.screen as Screen.Result).outcome.kind)
        assertEquals(0, fake.rejectedWithoutToken)
    }

    @Test fun theBankRefusesWhatCameWithoutItsCookie() {
        val vm = vm()
        vm.refreshHome()   // never signed in, so no cookie
        assertEquals(1, fake.rejectedWithoutCookie)
        assertFalse(fake.paid)
    }

    @Test fun anUnreachableBankIsAReadableMessage() {
        val settings = InMemorySettings(baseUrl = "http://127.0.0.1:9")
        val vm = vm(settings)
        vm.go(Screen.SignIn)
        vm.signIn("alice", "pw")
        assertEquals(Screen.SignIn, vm.screen)
        assertTrue(vm.error!!.startsWith("Cannot reach 127.0.0.1:9"))
    }
}
