package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.AmfaLink
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.logic.deviceOutcome
import com.adaptivemfa.app.logic.registrationProblems
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

/** Things found by driving the app on an emulator: each of these was a real defect. */
@OptIn(ExperimentalCoroutinesApi::class)
class FrontendFixesTest {
    private val dispatcher = UnconfinedTestDispatcher()
    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun me(trusted: Boolean) = ok("""{"name":"Asha Rao","balance":50000000,"enrolled":null,"protection":{"level":"Basic"},"payees":[],"deviceTrusted":$trusted}""")
    private val started = HttpResult(201, """{"action":{"id":"ACT5","kind":"device","status":"AWAITING_VERIFICATION","title":"Verified a new device","amountMinor":0,"checks":[],"payee":{"display":"This device"}},"challengeUrl":null}""")
    private val settled = ok("""{"action":{"id":"ACT5","kind":"device","status":"DONE","title":"Verified a new device","amountMinor":0,"payee":{"display":"This device"}},"balance":50000000}""")

    private fun signedIn(http: RoutedHttp): AppViewModel {
        val s = InMemorySettings()
        return AppViewModel(s, BankApi(http, { s.baseUrl }, { s.deviceId }), { link -> AmfaApi(http, link) }, dispatcher).also { it.signIn("asha", "pw") }
    }

    @Test fun aJsonNullIsNeverShownAsTheTextNull() {
        val state = """{"status":"PENDING","approving":{"amount":"₹5.00","payee":"V"},"steps":[{"factor":"UEBA","status":"PENDING"}],"challenge":{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"UEBA","params":{"phrase":null,"mode":null,"sentTo":null}}}"""
        val http = RoutedHttp("GET /c/v1/session" to ok(state),
            "POST /c/v1/session/factors/UEBA" to ok("""{"result":{"factor":"UEBA","status":"FAILED","retry":true,"guidance":null},"escalation":null,"state":$state}"""))
        val api = AmfaApi(http, AmfaLink("http://amfa:8040", "t"))
        val ch = api.session().challenge!!
        assertNull(ch.phrase); assertNull(ch.mode); assertNull(ch.sentTo)
        assertNull(api.submit("UEBA", ch.id, JSONObject()).guidance)   // the screen then says its own "try again"
    }

    @Test fun aPhoneTheBankHasNotVerifiedIsKnownSoTheAppCanAskForItsVerification() {
        val http = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(false), "GET /api/activity" to ok("""{"activity":[]}"""))
        assertTrue(signedIn(http).untrusted)
        http.routes["GET /api/me"] = me(true)
        assertFalse(signedIn(http).untrusted)
    }

    @Test fun verifyingThisPhoneIsAReviewedAndCheckedAction() {
        val http = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(false), "GET /api/activity" to ok("""{"activity":[]}"""),
            "POST /api/actions/device" to started, "POST /api/actions/ACT5/complete" to settled)
        val vm = signedIn(http)
        vm.startDevice()
        assertTrue(vm.screen is Screen.Review)
        assertTrue(JSONObject(http.last("/api/actions/device").body!!).getString("deviceId").isNotBlank())
        http.routes["GET /api/me"] = me(true)
        vm.confirmPayment()                                              // no checks asked: the bank settles it at once
        assertEquals(deviceOutcome().title, (vm.screen as Screen.Result).outcome.title)
        assertFalse(vm.untrusted)
    }

    @Test fun cancellingTheVerificationDoesNotTalkAboutPayments() {
        val http = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(false), "GET /api/activity" to ok("""{"activity":[]}"""),
            "POST /api/actions/device" to started, "POST /api/actions/ACT5/cancel" to ok("""{"action":{"id":"ACT5","kind":"device","status":"CANCELLED"}}"""))
        val vm = signedIn(http)
        vm.startDevice(); vm.cancelReview()
        assertEquals("Cancelled. This phone is not verified yet.", vm.notice)
    }

    @Test fun aNoticeFromAnEarlierScreenIsGoneOnceTheNextRequestStarts() {
        val http = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(true), "GET /api/activity" to ok("""{"activity":[]}"""),
            "POST /api/actions/device" to started)
        val vm = signedIn(http)
        vm.notice = "Payment cancelled. Nothing was sent."
        vm.startDevice()
        assertNull(vm.notice)
    }

    @Test fun everySignUpProblemIsKeyedToItsOwnFieldInFormOrder() {
        val all = registrationProblems("Al", "A", "123", "nope", "short", "short")
        assertEquals(listOf("name", "email", "phone", "username", "password"), all.keys.toList())
        assertTrue(registrationProblems("alice", "Alice Rao", "9812345678", "a@example.com", "longenough1", "longenough1").isEmpty())
        assertEquals(mapOf("password" to "Passwords do not match"), registrationProblems("alice", "Alice Rao", "9812345678", "a@example.com", "longenough1", "other"))
    }

    @Test fun aSlowServerIsReportedAsSlowNotAsMissing() {
        val slow = object : com.adaptivemfa.app.data.Http {
            override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult = throw java.net.SocketTimeoutException("Read timed out")
        }
        val gone = object : com.adaptivemfa.app.data.Http {
            override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult = throw java.net.ConnectException("refused")
        }
        fun say(h: com.adaptivemfa.app.data.Http) = try { BankApi(h, { "http://bank:8050/" }, { "android_test01" }).ping(); "" } catch (e: com.adaptivemfa.app.data.ApiException) { e.message }
        assertTrue(say(slow).contains("taking too long"))
        assertTrue(say(gone).contains("Cannot reach"))
    }
}
