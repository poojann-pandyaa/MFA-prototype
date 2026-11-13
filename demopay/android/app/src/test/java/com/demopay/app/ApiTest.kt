package com.demopay.app

import com.demopay.app.data.Api
import com.demopay.app.data.ApiException
import com.demopay.app.data.Http
import com.demopay.app.data.HttpResult
import com.demopay.app.data.RiskRequest
import com.demopay.app.data.parseError
import com.demopay.app.logic.Signals
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

private class FakeHttp(var result: HttpResult = HttpResult(200, "{}"), var fail: IOException? = null) : Http {
    var method = ""; var url = ""; var headers: Map<String, String> = emptyMap(); var body: String? = null
    override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult {
        this.method = method; this.url = url; this.headers = headers; this.body = body
        fail?.let { throw it }
        return result
    }
}

private const val PAYMENT = """{"id":15,"status":"pending","amount":20000.0,"payee_vpa":"officer.verify@upi","registered_name":"Verification Cell","channel":"pay","note":"",
"severity":"HIGH","threat":"COACHED","plan":{"decision":"steps","p_scam":0.01061,"base_rate":0.0001,"expected_cost":118.05,"time_seconds":22.0,"notes":["Slow steps are kept for the riskiest payments only."],
"steps":[{"index":0,"method":"payee_check","name":"Payee check","tier":"fast","why":"Checks the registered name.","est_seconds":6.0,"doubt_after":0.0092},
{"index":1,"method":"typed_confirm","name":"Type the payee's registered name","tier":"fast","why":"w","est_seconds":8.0,"doubt_after":0.008}]},
"steps_done":0,"current_step":0,"message":"","created_at":"2026-11-06T12:00:00Z"}"""

class ApiTest {
    private fun api(h: Http, token: String? = "tok") = Api(h, { "http://10.0.2.2:8070/" }, { token }, { "and-1234" })

    @Test fun requestCarriesTokenAndDeviceId() {
        val h = FakeHttp(HttpResult(200, PAYMENT))
        api(h).payment(15)
        assertEquals("GET", h.method); assertEquals("http://10.0.2.2:8070/pay/15", h.url)
        assertEquals("Bearer tok", h.headers["Authorization"]); assertEquals("and-1234", h.headers["X-Device-Id"])
    }

    @Test fun noTokenMeansNoAuthorizationHeader() {
        val h = FakeHttp(HttpResult(200, """{"token":"t"}"""))
        assertEquals("t", api(h, token = null).login("a", "b"))
        assertFalse(h.headers.containsKey("Authorization"))
        assertEquals("application/json", h.headers["Content-Type"])
    }

    @Test fun paymentJsonFromTheServerIsParsed() {
        val p = api(FakeHttp(HttpResult(200, PAYMENT))).payment(15)
        assertEquals("pending", p.status); assertEquals(0, p.currentStep); assertEquals("COACHED", p.threat)
        assertEquals(2, p.plan.steps.size); assertEquals("payee_check", p.plan.steps[0].method); assertEquals(0.0092, p.plan.steps[0].doubtAfter, 1e-9)
        assertEquals("Verification Cell", p.registeredName)
    }

    @Test fun nullsInThePaymentAreHandled() {
        val p = api(FakeHttp(HttpResult(200, """{"id":1,"status":"approved","amount":5.0,"payee_vpa":"x@y","registered_name":null,"channel":"pay","severity":"LOW","threat":"NONE",
            "plan":{"decision":"allow","p_scam":0.0,"base_rate":0.0001,"expected_cost":0.0,"time_seconds":0.0,"notes":[],"steps":[]},"steps_done":0,"current_step":null,"message":""}"""))).payment(1)
        assertNull(p.registeredName); assertNull(p.currentStep); assertTrue(p.plan.steps.isEmpty())
    }

    @Test fun riskRequestBodyMatchesTheServerContract() {
        val h = FakeHttp(HttpResult(200, PAYMENT))
        api(h).risk(RiskRequest("a@ybl", 4000.0, "collect", false, true, Signals(callActive = true, callMinutes = 25.0), 0.001))
        val b = JSONObject(h.body!!)
        assertEquals("a@ybl", b.getString("payee_vpa")); assertEquals(4000.0, b.getDouble("amount"), 0.0); assertEquals("collect", b.getString("channel"))
        assertTrue(b.getBoolean("attested")); assertEquals(0.001, b.getDouble("base_rate"), 0.0)
        assertTrue(b.getJSONObject("signals").getBoolean("call_active")); assertFalse(b.getJSONObject("signals").has("remote_access_active"))
        assertFalse(JSONObject(RiskRequest("a@ybl", 1.0).toJson().toString()).has("base_rate"))
    }

    @Test fun queryValuesAreEncoded() {
        val h = FakeHttp(HttpResult(200, """{"found":true,"registered_name":"P Sharma"}"""))
        val l = api(h).lookup("priya sharma@okaxis")
        assertEquals("http://10.0.2.2:8070/directory/lookup?vpa=priya+sharma%40okaxis", h.url)
        assertTrue(l.found)
    }

    @Test fun serverErrorsBecomeReadableMessages() {
        assertEquals("Not enough balance", parseError(400, """{"detail":"Not enough balance"}""").message)
        val e = parseError(400, """{"detail":{"code":"mismatch","message":"This answer does not belong to this payment."}}""")
        assertEquals("mismatch", e.code); assertEquals("This answer does not belong to this payment.", e.message)
        assertEquals("String should match pattern; value is required", parseError(422, """{"detail":[{"msg":"String should match pattern"},{"msg":"value is required"}]}""").message)
        assertEquals("Request failed (500)", parseError(500, "<html>oops</html>").message)
    }

    @Test fun httpFailureIsReportedWithTheAddress() {
        try {
            api(FakeHttp(fail = IOException("boom"))).me(); fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(0, e.status); assertTrue(e.message.contains("http://10.0.2.2:8070"))
        }
    }

    @Test fun nonSuccessStatusThrowsWithTheServerMessage() {
        try {
            api(FakeHttp(HttpResult(409, """{"detail":"This payment is blocked"}"""))).complete(3, "1234"); fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(409, e.status); assertEquals("This payment is blocked", e.message)
        }
    }

    @Test fun xrayAndStepResultsAreParsed() {
        val x = api(FakeHttp(HttpResult(200, """{"severity":"HIGH","threat":"COACHED","model":"full","score":6.7,"surprises":{"takeover":5.0,"coached":7.9,"payee":6.7},
            "thresholds":{"MEDIUM":4.6,"HIGH":6.6,"CRITICAL":8.3},"reasons":[{"input":"payee_age_days","pushes_up_by":6.52}],"inputs":{"amount":20000.0,"channel":"pay","call_active":true},
            "plan":{"decision":"steps","p_scam":0.01,"base_rate":0.0001,"expected_cost":1.0,"time_seconds":6.0,"notes":[],"steps":[]}}"""))).xray(15)
        assertEquals(7.9, x.surprises["coached"]!!, 0.0); assertEquals(6.6, x.thresholds!!["HIGH"]!!, 0.0)
        assertEquals("payee_age_days", x.reasons[0].first); assertEquals("true", x.inputs["call_active"])
        val r = api(FakeHttp(HttpResult(200, """{"passed":false,"retry":true,"message":"That does not match.","attempts_left":2,"status":"pending"}"""))).answerStep(1, 1, JSONObject().put("typed", "x"))
        assertTrue(r.retry); assertEquals(2, r.attemptsLeft)
    }
}
