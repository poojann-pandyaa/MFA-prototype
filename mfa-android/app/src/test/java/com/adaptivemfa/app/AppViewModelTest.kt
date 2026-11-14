package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.ui.AppViewModel
import com.adaptivemfa.app.ui.Screen
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private val ME_FACE = ok("""{"name":"Alice Rao","balance":50000000,"enrolled":{"FACE":"ENROLLED"},"protection":{"level":"Good"},"payees":[{"ref":"BEN-42","display":"V. Mehta"}]}""")
private val ME_NO_FACE = ok("""{"name":"Alice Rao","balance":50000000,"enrolled":null,"protection":{"level":"Basic"},"payees":[{"ref":"BEN-42","display":"V. Mehta"}]}""")
private val ACTIVITY = ok("""{"activity":[{"id":"ACT0","title":"Sent to V. Mehta","amountMinor":100000,"status":"DONE","payee":{"display":"V. Mehta"}}],"balance":50000000}""")
private val LOGIN = ok("""{"signedIn":true}""")
private const val CHALLENGE_URL = "http://127.0.0.1:8040/hosted/challenge?t=demo-bank&embed=1#token=sess-tok"
private val NO_CHECK = HttpResult(201, """{"action":{"id":"ACT1","status":"AWAITING_VERIFICATION"},"challengeUrl":null}""")
private val FACE_CHECK = HttpResult(201, """{"action":{"id":"ACT1","status":"AWAITING_VERIFICATION"},"challengeUrl":"$CHALLENGE_URL"}""")
private const val CH_FACE = """{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"FACE","params":{"action":"TURN_LEFT","frames":20,"intervalMs":150}}"""
private fun session(status: String, challenge: String?) = """{"status":"$status","approving":{"amount":"₹5,000.00","payee":"V. Mehta"},"steps":[{"factor":"FACE","status":"PENDING"}],"challenge":${challenge ?: "null"}}"""
private fun submitted(factorStatus: String, retry: Boolean, guidance: String?, state: String) =
    ok("""{"result":{"factor":"FACE","status":"$factorStatus","retry":$retry,"guidance":${guidance?.let { "\"$it\"" } ?: "null"}},"escalation":null,"state":$state}""")
private fun done(status: String, detail: String? = null) =
    ok("""{"action":{"id":"ACT1","status":"$status","detail":${detail?.let { "\"$it\"" } ?: "null"}},"balance":49500000}""")
private val FRAMES20 = List(20) { "data:image/jpeg;base64,AAAA" }
private val FRAMES5 = List(5) { "data:image/jpeg;base64,AAAA" }

@OptIn(ExperimentalCoroutinesApi::class)
class AppViewModelTest {
    private val dispatcher = UnconfinedTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm(http: RoutedHttp, settings: InMemorySettings = InMemorySettings()) =
        AppViewModel(settings, BankApi(http, { settings.baseUrl }, { settings.deviceId }), { link -> AmfaApi(http, link) }, dispatcher)

    private fun signedIn(http: RoutedHttp): AppViewModel = vm(http).also { it.login("alice", "pw") }

    @Test fun startsOnLogin() {
        assertEquals(Screen.Login, vm(RoutedHttp()).screen)
    }

    @Test fun loginOpensTheHomeScreenWithBalanceAndActivity() {
        val s = InMemorySettings()
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY)
        val vm = vm(http, s)
        vm.login("  alice ", "pw")
        assertEquals(Screen.Dashboard, vm.screen)
        assertEquals("alice", JSONObject(http.last("/api/login").body!!).getString("username"))
        assertEquals("alice", s.username)
        assertEquals(50_000_000L, vm.me!!.balanceMinor)
        assertEquals(1, vm.activity.size)
        assertFalse(vm.busy)
    }

    @Test fun wrongPasswordStaysOnLoginWithTheBanksMessage() {
        val vm = vm(RoutedHttp("POST /api/login" to HttpResult(401, """{"detail":"wrong username or password"}""")))
        vm.login("alice", "nope")
        assertEquals(Screen.Login, vm.screen)
        assertEquals("wrong username or password", vm.error)
    }

    @Test fun openingAnAccountLeadsStraightIntoFaceSetupThenHome() {
        val http = RoutedHttp(
            "POST /api/register" to HttpResult(201, """{"signedIn":true}"""),
            "GET /api/me" to ME_NO_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/amfa/enrol" to ok("""{"url":"http://127.0.0.1:8040/hosted/enroll?t=demo-bank&embed=1#token=enrol-tok"}"""),
            "GET /c/v1/enrollment" to ok("""{"consent":{"required":true,"version":"2026-09-v1"},"enrolled":{"FACE":false}}"""),
            "POST /c/v1/enrollment/consent" to ok("""{"consented":true}"""),
            "POST /c/v1/enrollment/face" to ok("""{"enrolled":true,"factor":"FACE","quality":{}}"""),
            "POST /c/v1/enrollment/complete" to ok("""{"complete":true,"enrolled":["FACE"]}"""),
        )
        val vm = vm(http)
        vm.register("Alice", "Alice Rao", "9812345678", "a@example.com", "longenough1")
        val setup = vm.screen as Screen.FaceSetup
        assertTrue(setup.consentRequired)
        // the bank's host, not AMFA's own 127.0.0.1, because the phone can only reach the machine the bank is on
        assertTrue(http.last("/c/v1/enrollment").url.startsWith("http://10.0.2.2:8040/"))
        assertEquals("Bearer enrol-tok", http.last("/c/v1/enrollment").headers["Authorization"])
        assertEquals("alice", JSONObject(http.last("/api/register").body!!).getString("username"))

        vm.submitFaceSetup(FRAMES5)
        assertTrue(http.called("/enrollment/consent"))
        assertEquals(5, JSONObject(http.last("/enrollment/face").body!!).getJSONArray("frames").length())
        assertTrue(http.called("/enrollment/complete"))
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.notice!!.contains("Face check set up"))
    }

    @Test fun aRejectedFaceSetupStaysOnTheScreenWithAReason() {
        val http = RoutedHttp(
            "POST /api/register" to HttpResult(201, "{}"), "GET /api/me" to ME_NO_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/amfa/enrol" to ok("""{"url":"http://127.0.0.1:8040/hosted/enroll#token=t"}"""),
            "GET /c/v1/enrollment" to ok("""{"consent":{"required":false,"version":"v"},"enrolled":{"FACE":false}}"""),
            "POST /c/v1/enrollment/face" to ok("""{"enrolled":false,"factor":"FACE","reason":"LIVENESS_NOT_ESTABLISHED"}"""),
        )
        val vm = vm(http)
        vm.register("alice", "Alice Rao", "9812345678", "a@example.com", "longenough1")
        vm.submitFaceSetup(FRAMES5)
        assertTrue(vm.screen is Screen.FaceSetup)
        assertTrue(vm.error!!.contains("light"))
        assertFalse(http.called("/enrollment/complete"))
        assertFalse(http.called("/enrollment/consent"))   // already consented
    }

    @Test fun aPaymentAmfaDoesNotChallengeIsSettledAtOnce() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to NO_CHECK, "POST /api/actions/ACT1/complete" to done("DONE"))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 500.0)
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.notice!!.startsWith("Payment sent"))
        assertNull(vm.error)
    }

    @Test fun aChallengedPaymentGoesThroughTheFaceCheckAndIsThenSettledByTheBank() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /c/v1/session/factors/FACE" to submitted("PASSED", false, null, session("VERIFIED", null)),
            "POST /api/actions/ACT1/complete" to done("DONE"))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 5000.0)
        val v = vm.screen as Screen.Verify
        assertEquals("TURN_LEFT", v.session.challenge!!.action)
        assertEquals("Bearer sess-tok", http.last("/c/v1/session").headers["Authorization"])
        assertTrue(http.last("/c/v1/session").url.startsWith("http://10.0.2.2:8040/"))

        vm.submitFace(FRAMES20)
        val sent = JSONObject(http.last("/factors/FACE").body!!)
        assertEquals("ch_aaaaaaaaaaaaaaaaaaaa", sent.getString("challengeId"))
        assertEquals(20, sent.getJSONObject("payload").getJSONArray("frames").length())
        assertTrue(http.called("/api/actions/ACT1/complete"))
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.notice!!.contains("Payment sent"))
    }

    @Test fun aFailedFaceCheckAllowsAnotherTryWithTheServersGuidance() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /c/v1/session/factors/FACE" to submitted("FAILED", true, "Face a light and try again", session("PENDING", CH_FACE)))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 5000.0)
        vm.submitFace(FRAMES20)
        assertTrue(vm.screen is Screen.Verify)
        assertEquals("Face a light and try again", vm.error)
        assertFalse(http.called("/api/actions/ACT1/complete"))
    }

    @Test fun aSessionThatEndsBlockedIsSettledAndReportedAsAFailure() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /c/v1/session/factors/FACE" to submitted("SPOOF_DETECTED", false, null, session("BLOCKED", null)),
            "POST /api/actions/ACT1/complete" to done("DECLINED", "blocked after repeated failed checks"))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 5000.0)
        vm.submitFace(FRAMES20)
        assertEquals(Screen.Dashboard, vm.screen)
        assertTrue(vm.error!!.startsWith("The payment was declined"))
        assertNull(vm.notice)
    }

    @Test fun aCodeCheckSendsTheSixDigits() {
        val ch = """{"challengeId":"ch_bbbbbbbbbbbbbbbbbbbb","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com"}}"""
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", ch)),
            "POST /c/v1/session/factors/OTP" to submitted("PASSED", false, null, session("VERIFIED", null)),
            "POST /api/actions/ACT1/complete" to done("DONE"))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 5000.0)
        vm.submitCode(" 123456 ")
        assertEquals("123456", JSONObject(http.last("/factors/OTP").body!!).getJSONObject("payload").getString("code"))
        assertEquals(Screen.Dashboard, vm.screen)
    }

    @Test fun cancellingAPaymentTellsTheBankAndReturnsHome() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /api/actions/ACT1/cancel" to done("CANCELLED"))
        val vm = signedIn(http)
        vm.sendMoney("BEN-42", 5000.0)
        vm.back()
        assertTrue(http.called("/api/actions/ACT1/cancel"))
        assertEquals(Screen.Dashboard, vm.screen)
    }

    @Test fun anExpiredBankSessionSignsTheCustomerOut() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY)
        val vm = signedIn(http)
        http.routes["GET /api/me"] = HttpResult(401, """{"detail":"sign in to the bank first"}""")
        vm.refreshHome()
        assertEquals(Screen.Login, vm.screen)
        assertTrue(vm.error!!.contains("expired"))
        assertNull(vm.me)
    }

    @Test fun signingOutClearsTheHomeData() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/logout" to ok("""{"signedIn":false}"""))
        val vm = signedIn(http)
        vm.logout()
        assertEquals(Screen.Login, vm.screen)
        assertNull(vm.me)
        assertTrue(vm.activity.isEmpty())
    }

    @Test fun settingsKeepAUsableAddressOnly() {
        val s = InMemorySettings()
        val vm = vm(RoutedHttp("GET /assets/config.json" to ok("""{"amfaBase":"x"}""")), s)
        assertFalse(vm.saveBaseUrl("not an address"))
        assertTrue(vm.error!!.contains("does not look like"))
        assertEquals("http://10.0.2.2:8050", s.baseUrl)
        assertTrue(vm.saveBaseUrl("192.168.1.20:8050/"))
        assertEquals("http://192.168.1.20:8050", s.baseUrl)
        vm.testConnection("192.168.1.20:8050")
        assertTrue(vm.notice!!.startsWith("Connected"))
    }

    @Test fun settingsReturnsToTheScreenItCameFrom() {
        val vm = vm(RoutedHttp())
        vm.go(Screen.Register)
        vm.go(Screen.Settings)
        vm.back()
        assertEquals(Screen.Register, vm.screen)
    }
}
