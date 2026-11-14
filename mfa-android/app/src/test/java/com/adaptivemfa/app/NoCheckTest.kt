package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.logic.noCheckOutcome
import com.adaptivemfa.app.logic.parseNoCheck
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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private fun me(noCheck: String) = ok("""{"name":"Alice Rao","balance":50000000,"enrolled":null,"protection":{"level":"Basic"},"payees":[],"noCheck":$noCheck}""")
private val STARTED = HttpResultOf("""{"action":{"id":"ACT9","kind":"nocheck","status":"AWAITING_VERIFICATION","title":"No extra checks up to ₹3,000 a day","amountMinor":300000,"checks":[],"payee":{"display":"No-extra-check limit","initials":"NL"}},"challengeUrl":null}""")
private fun HttpResultOf(body: String) = com.adaptivemfa.app.data.HttpResult(201, body)
private fun settled(status: String = "DONE", detail: String? = null) =
    ok("""{"action":{"id":"ACT9","kind":"nocheck","status":"$status","amountMinor":300000,"payee":{"display":"No-extra-check limit"},"detail":${detail?.let { "\"$it\"" } ?: "null"}},"balance":50000000}""")

@OptIn(ExperimentalCoroutinesApi::class)
class NoCheckTest {
    private val dispatcher = UnconfinedTestDispatcher()
    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun routes(noCheck: String = "null") = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(noCheck),
        "GET /api/activity" to ok("""{"activity":[]}"""), "POST /api/actions/nocheck" to STARTED, "POST /api/actions/ACT9/complete" to settled())

    private fun signedIn(http: RoutedHttp): AppViewModel {
        val s = InMemorySettings()
        return AppViewModel(s, BankApi(http, { s.baseUrl }, { s.deviceId }), { link -> AmfaApi(http, link) }, dispatcher).also { it.signIn("alice", "pw") }
    }

    @Test fun onlyWholeRupeesInTheAllowedRangeAreAccepted() {
        assertEquals(3000, parseNoCheck("3000")); assertEquals(3000, parseNoCheck(" 3,000 "))
        assertEquals(500, parseNoCheck("500")); assertEquals(50_000, parseNoCheck("50000"))
        for (bad in listOf("", "499", "50001", "12.5", "abc", "-5", "0")) assertNull(bad, parseNoCheck(bad))
    }

    @Test fun theResultSaysWhenARaiseWaits() {
        assertEquals("No extra checks up to ₹3,000 a day", noCheckOutcome("₹3,000", null).title)
        val later = noCheckOutcome("₹8,000", 23)
        assertEquals("Your new limit starts later", later.title); assertTrue(later.text.contains("23 hours"))
        assertTrue(noCheckOutcome("₹8,000", 0).text.contains("1 hours"))      // never "0 hours"
    }

    @Test fun theBanksLimitIsReadFromTheProfileData() {
        val vm = signedIn(routes("""{"limit":300000,"pending":800000,"pendingFrom":1900000000}"""))
        assertEquals(300000L, vm.me!!.noCheckMinor); assertEquals(800000L, vm.me!!.noCheckPendingMinor)
        assertEquals(0L, signedIn(routes()).me!!.noCheckMinor); assertNull(signedIn(routes()).me!!.noCheckPendingMinor)
    }

    @Test fun settingTheLimitIsAReviewedAndSettledAction() {
        val http = routes()
        val vm = signedIn(http)
        vm.startNoCheck(3000)
        assertEquals(3000.0, JSONObject(http.last("/api/actions/nocheck").body!!).getDouble("limitRupees"), 0.0)
        assertTrue(vm.screen is Screen.Review)
        http.routes["GET /api/me"] = me("""{"limit":300000,"pending":null,"pendingFrom":null}""")
        vm.confirmPayment()                                              // no checks asked: the bank settles it at once
        val r = vm.screen as Screen.Result
        assertEquals("No extra checks up to ₹3,000.00 a day", r.outcome.title)
        assertEquals(300000L, vm.me!!.noCheckMinor)
    }

    @Test fun aRaiseThatMustWaitIsExplained() {
        val http = routes()
        val vm = signedIn(http)
        vm.startNoCheck(8000)
        http.routes["GET /api/me"] = me("""{"limit":200000,"pending":800000,"pendingFrom":${System.currentTimeMillis() / 1000.0 + 23.5 * 3600}}""")
        vm.confirmPayment()
        assertEquals("Your new limit starts later", (vm.screen as Screen.Result).outcome.title)
    }

    @Test fun aFailedLimitChangeSaysNothingWasChanged() {
        val http = routes()
        http.routes["POST /api/actions/ACT9/complete"] = settled("FAILED", "the limit could not be saved (BYPASS_ABOVE_MAX)")
        val vm = signedIn(http)
        vm.startNoCheck(5000); vm.confirmPayment()
        val o = (vm.screen as Screen.Result).outcome
        assertEquals("bad", o.kind); assertTrue(o.text.contains("BYPASS_ABOVE_MAX"))
    }

    @Test fun cancellingTheReviewReturnsToProfileWithTheRightWords() {
        val http = routes(); http.routes["POST /api/actions/ACT9/cancel"] = ok("{}")
        val vm = signedIn(http)
        vm.startNoCheck(3000); vm.back()
        assertEquals(Screen.Profile, vm.screen); assertEquals("Cancelled. Nothing was changed.", vm.notice)
    }

    @Test fun turningItOffIsImmediate() {
        val http = routes("""{"limit":300000,"pending":null,"pendingFrom":null}""")
        http.routes["DELETE /api/nocheck-limit"] = ok("""{"noCheck":null}""")
        val vm = signedIn(http)
        http.routes["GET /api/me"] = me("null")
        vm.removeNoCheck()
        assertTrue(http.called("/api/nocheck-limit")); assertEquals(Screen.Profile, vm.screen); assertEquals(0L, vm.me!!.noCheckMinor)
    }
}
