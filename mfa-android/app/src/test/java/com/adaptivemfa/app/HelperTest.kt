package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.logic.helperOutcome
import com.adaptivemfa.app.logic.parseHelperCode
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

private fun me(helpers: String) = ok("""{"name":"Asha Rao","balance":50000000,"enrolled":null,"protection":{"level":"Basic"},"payees":[],"helpers":$helpers}""")
private val ON = """{"enabled":true,"max":2,"helpers":[]}"""
private val STARTED = HttpResult(201, """{"action":{"id":"ACT7","kind":"helper","status":"AWAITING_VERIFICATION","title":"Allow Son to help with payments","amountMinor":0,"checks":[],"payee":{"display":"Family helper","initials":"FH"}},"challengeUrl":null}""")
private val SETTLED = ok("""{"action":{"id":"ACT7","kind":"helper","status":"DONE","title":"Allow Son to help with payments","amountMinor":0,"payee":{"display":"Family helper"},"helperCode":"12345678"},"balance":50000000}""")

@OptIn(ExperimentalCoroutinesApi::class)
class HelperTest {
    private val dispatcher = UnconfinedTestDispatcher()
    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun routes(helpers: String = ON) = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to me(helpers),
        "GET /api/activity" to ok("""{"activity":[]}"""), "POST /api/actions/helper" to STARTED, "POST /api/actions/ACT7/complete" to SETTLED,
        "POST /api/helpers/redeem" to ok(ON), "DELETE /api/helpers/hlp_1" to ok(ON))

    private fun signedIn(http: RoutedHttp): AppViewModel {
        val s = InMemorySettings()
        return AppViewModel(s, BankApi(http, { s.baseUrl }, { s.deviceId }), { link -> AmfaApi(http, link) }, dispatcher).also { it.signIn("asha", "pw") }
    }

    @Test fun onlyAnEightDigitCodeIsAccepted() {
        assertEquals("12345678", parseHelperCode("1234 5678")); assertEquals("12345678", parseHelperCode("12345678"))
        for (bad in listOf("", "1234567", "123456789", "abcdefgh")) assertNull(bad, parseHelperCode(bad))
    }

    @Test fun helpersAreReadFromTheBanksAnswerAndAreAbsentWhenTheBankDoesNotOfferThem() {
        val vm = signedIn(routes("""{"enabled":true,"max":2,"helpers":[{"id":"hlp_1","label":"Son","state":"WAITING"}]}"""))
        assertEquals(listOf("Son"), vm.me!!.helpers!!.helpers.map { it.label }); assertEquals("WAITING", vm.me!!.helpers!!.helpers[0].state)
        assertNull(signedIn(routes("""{"enabled":false,"max":2,"helpers":[]}""")).me!!.helpers)
    }

    @Test fun addingAHelperIsAReviewedAndCheckedActionThatEndsWithTheCode() {
        val http = routes()
        val vm = signedIn(http)
        vm.startHelper("Son")
        assertEquals("Son", JSONObject(http.last("/api/actions/helper").body!!).getString("label"))
        assertTrue(vm.screen is Screen.Review)
        vm.confirmPayment()                                              // no checks asked: the bank settles it at once
        val r = vm.screen as Screen.Result
        assertEquals("Ready for Son", r.outcome.title); assertEquals("12345678", r.action!!.helperCode)
    }

    @Test fun cancellingTheReviewLeavesNothingChangedAndGoesBackToProfile() {
        val http = routes(); http.routes["POST /api/actions/ACT7/cancel"] = ok("""{"action":{"id":"ACT7","kind":"helper","status":"CANCELLED"}}""")
        val vm = signedIn(http)
        vm.startHelper("Son"); vm.cancelReview()
        assertEquals(Screen.Profile, vm.screen)
    }

    @Test fun theHelperPhoneRedeemsTheCodeWithItsOwnDeviceIdAndAHelperCanBeRemoved() {
        val http = routes()
        val vm = signedIn(http)
        vm.redeemHelper("12345678")
        val body = JSONObject(http.last("/api/helpers/redeem").body!!)
        assertEquals("12345678", body.getString("code")); assertTrue(body.getString("deviceId").isNotBlank())
        vm.removeHelper("hlp_1")
        assertTrue(http.calls.any { it.method == "DELETE" && it.url.endsWith("/api/helpers/hlp_1") })
    }

    @Test fun theResultExplainsWhatTheFamilyMemberDoes() {
        assertTrue(helperOutcome("Son").text.contains("enter the code"))
        assertEquals("Ready for your helper", helperOutcome("").title)
    }
}
