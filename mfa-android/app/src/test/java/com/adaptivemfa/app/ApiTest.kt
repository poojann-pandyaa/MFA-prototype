package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.AmfaLink
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.data.parseAmfaLink
import com.adaptivemfa.app.data.parseError
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ApiTest {
    private fun bank(http: RoutedHttp) = BankApi(http, { "http://bank:8050/" }, { "android_test01" })
    private val link = AmfaLink("http://amfa:8040", "tok-1")

    @Test fun amfaLinkIsSplitIntoAddressAndFragmentToken() {
        val l = parseAmfaLink("http://127.0.0.1:8040/hosted/challenge?t=demo-bank&embed=1#token=abc123")
        assertEquals("http://127.0.0.1:8040", l.base)
        assertEquals("abc123", l.token)
        assertEquals("https://amfa.example.com", parseAmfaLink("https://amfa.example.com/hosted/enroll#token=x&y=1").base)
        try { parseAmfaLink("http://h/hosted/challenge?t=a"); fail() } catch (e: ApiException) { assertTrue(e.message.contains("token")) }
    }

    @Test fun errorsFromBankAndAmfaBothReadAsText() {
        assertEquals("wrong username or password", parseError(401, """{"detail":"wrong username or password"}""").message)
        assertEquals("too_short; bad", parseError(422, """{"detail":[{"msg":"too_short"},{"msg":"bad"}]}""").message)
        val e = parseError(409, """{"error":{"code":"SESSION_EXPIRED","message":"session expired"}}""")
        assertEquals("session expired", e.message)
        assertEquals("SESSION_EXPIRED", e.code)
        assertEquals("Request failed (500)", parseError(500, "<html>").message)
    }

    @Test fun registerSendsTheBanksFieldsAndThisPhonesId() {
        val http = RoutedHttp("POST /api/register" to HttpResult(201, """{"signedIn":true}"""))
        bank(http).register("alice", "Alice Rao", "9812345678", "a@example.com", "longenough1")
        val body = JSONObject(http.last("/api/register").body!!)
        assertEquals("android_test01", body.getString("deviceId"))
        assertEquals("alice", body.getString("username"))
        assertEquals("http://bank:8050/api/register", http.calls.single().url)
    }

    @Test fun meReadsBalanceFaceStateAndPayees() {
        val http = RoutedHttp("GET /api/me" to ok("""{"name":"Alice","balance":50000000,"enrolled":{"FACE":"ENROLLED"},
            "protection":{"level":"Good"},"frozen":false,"payees":[{"ref":"BEN-42","display":"V. Mehta"}]}"""))
        val me = bank(http).me()
        assertEquals(50_000_000L, me.balanceMinor)
        assertTrue(me.faceEnrolled)
        assertEquals("BEN-42", me.payees.single().ref)
        assertTrue(http.calls.single().url.endsWith("/api/me?deviceId=android_test01"))
    }

    @Test fun meBeforeAnyAmfaSetupHasNoFace() {
        val http = RoutedHttp("GET /api/me" to ok("""{"name":"Alice","balance":1,"enrolled":null,"protection":{"level":"Basic"},"payees":[]}"""))
        assertFalse(bank(http).me().faceEnrolled)
    }

    @Test fun transferDeclaresWhatThePhoneCanDo() {
        val http = RoutedHttp("POST /api/actions/transfer" to HttpResult(201, """{"action":{"id":"ACT1","status":"AWAITING_VERIFICATION"},
            "challengeUrl":"http://127.0.0.1:8040/hosted/challenge?t=demo-bank&embed=1#token=sess"}"""))
        val started = bank(http).startTransfer("BEN-42", 1250.5)
        assertEquals("sess", started.link!!.token)
        val body = JSONObject(http.last("/transfer").body!!)
        assertEquals(1250.5, body.getDouble("amountRupees"), 0.0)
        assertTrue(body.getBoolean("camera") && body.getBoolean("touch"))
        assertFalse(body.getBoolean("microphone"))
        assertFalse(body.getBoolean("pointer"))
    }

    @Test fun transferWithNoCheckHasNoLink() {
        val http = RoutedHttp("POST /api/actions/transfer" to HttpResult(201, """{"action":{"id":"ACT2","status":"AWAITING_VERIFICATION"},"challengeUrl":null}"""))
        assertNull(bank(http).startTransfer("BEN-42", 10.0).link)
    }

    @Test fun completeReportsTheOutcomeAndBalance() {
        val http = RoutedHttp("POST /api/actions/ACT1/complete" to ok("""{"action":{"id":"ACT1","status":"DONE","detail":null},"balance":49500000}"""))
        val done = bank(http).complete("ACT1")
        assertEquals("DONE", done.status)
        assertNull(done.detail)
        assertEquals(49_500_000L, done.balanceMinor)
    }

    @Test fun enrolmentCallsUseTheBearerTokenAndAmfaPaths() {
        val http = RoutedHttp(
            "GET /c/v1/enrollment" to ok("""{"consent":{"required":true,"version":"2026-09-v1"},"enrolled":{"FACE":false}}"""),
            "POST /c/v1/enrollment/consent" to ok("""{"consented":true}"""),
            "POST /c/v1/enrollment/face" to ok("""{"enrolled":false,"reason":"LIVENESS_NOT_ESTABLISHED"}"""),
            "POST /c/v1/enrollment/complete" to ok("""{"complete":true}"""),
        )
        val api = AmfaApi(http, link)
        val st = api.enrollmentState()
        assertTrue(st.consentRequired)
        assertEquals("2026-09-v1", st.consentVersion)
        api.consent(st.consentVersion)
        assertEquals("2026-09-v1", JSONObject(http.last("/consent").body!!).getString("version"))
        assertEquals("LIVENESS_NOT_ESTABLISHED", api.enrollFace(listOf("a", "b", "c")))
        assertEquals(3, JSONObject(http.last("/enrollment/face").body!!).getJSONArray("frames").length())
        api.enrollComplete()
        assertTrue(http.calls.all { it.headers["Authorization"] == "Bearer tok-1" && it.url.startsWith("http://amfa:8040/c/v1/") })
    }

    @Test fun sessionAndSubmitReadTheChallengeAndOutcome() {
        val state = """{"status":"PENDING","approving":{"amount":"₹5,000.00","payee":"V. Mehta"},"steps":[{"factor":"FACE","status":"PENDING"}],
            "challenge":{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"FACE","params":{"action":"TURN_LEFT","frames":20,"intervalMs":150}}}"""
        val http = RoutedHttp(
            "GET /c/v1/session" to ok(state),
            "POST /c/v1/session/factors/FACE" to ok("""{"result":{"factor":"FACE","status":"FAILED","retry":true,"guidance":"Face a light"},"escalation":null,"state":$state}"""),
        )
        val api = AmfaApi(http, link)
        val s = api.session()
        val ch = s.challenge!!
        assertEquals("FACE", ch.factor)
        assertEquals("TURN_LEFT", ch.action)
        assertEquals(20, ch.frames)
        assertEquals("₹5,000.00", s.amount)
        val out = api.submit("FACE", ch.id, JSONObject().put("frames", org.json.JSONArray(listOf("x"))))
        assertEquals("FAILED", out.factorStatus)
        assertTrue(out.retry)
        assertEquals("Face a light", out.guidance)
        val sent = JSONObject(http.last("/factors/FACE").body!!)
        assertEquals("ch_aaaaaaaaaaaaaaaaaaaa", sent.getString("challengeId"))
        assertEquals(1, sent.getJSONObject("payload").getJSONArray("frames").length())
    }

    @Test fun otpChallengeCarriesItsModeAndMaskedAddress() {
        val http = RoutedHttp("GET /c/v1/session" to ok("""{"status":"PENDING","steps":[{"factor":"OTP","status":"PENDING"}],
            "challenge":{"challengeId":"ch_bbbbbbbbbbbbbbbbbbbb","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com","digits":6}}}"""))
        val ch = AmfaApi(http, link).session().challenge!!
        assertEquals("delivered", ch.mode)
        assertEquals("a***@example.com", ch.sentTo)
    }

    @Test fun serverDownBecomesAReadableError() {
        try { bank(RoutedHttp()).login("a", "b"); fail() } catch (e: ApiException) {
            assertEquals(0, e.status)
            assertTrue(e.message.contains("Cannot reach bank:8050"))
        }
    }

    @Test fun amfaErrorsKeepTheirCode() {
        val http = RoutedHttp("POST /c/v1/session/factors/FACE" to HttpResult(409, """{"error":{"code":"SESSION_EXPIRED","message":"session expired"}}"""))
        try { AmfaApi(http, link).submit("FACE", "ch_x", JSONObject()); fail() } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("SESSION_EXPIRED", e.code)
        }
    }
}
