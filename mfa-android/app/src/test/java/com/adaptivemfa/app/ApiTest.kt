package com.adaptivemfa.app

import com.adaptivemfa.app.data.Api
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.Http
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.data.Step1Result
import com.adaptivemfa.app.data.parseError
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

class ApiTest {
    private fun api(h: Http) = Api(h, { "http://10.0.2.2:8000/" }, { "android_abc123" })

    @Test fun step1KnownDeviceLogsInDirectly() {
        val h = FakeHttp(HttpResult(200, """{"require_step2":false,"access_token":"tok","token_type":"bearer","risk_score":"LOW"}"""))
        val r = api(h).loginStep1("alice", "pw")
        assertEquals(Step1Result.LoggedIn("LOW", "tok"), r)
        assertEquals("POST", h.method)
        assertEquals("http://10.0.2.2:8000/login/step1", h.url)
        val body = JSONObject(h.body!!)
        assertEquals("alice", body.getString("username"))
        assertEquals("pw", body.getString("password"))
        assertEquals("android_abc123", body.getString("device_identifier"))
        assertEquals("application/json", h.headers["Content-Type"])
        assertFalse(h.headers.containsKey("Authorization"))
    }

    @Test fun step1NewDeviceAsksForAFaceCheck() {
        val h = FakeHttp(HttpResult(200, """{"require_step2":true,"risk_score":"HIGH","session_id":"s-1","challenge_type":"turn_left"}"""))
        assertEquals(Step1Result.FaceCheckRequired("HIGH", "s-1", "turn_left"), api(h).loginStep1("alice", "pw"))
    }

    @Test fun step2SendsTheBurstWithSessionAndDevice() {
        val h = FakeHttp(HttpResult(200, """{"access_token":"tok2","token_type":"bearer"}"""))
        val frames = listOf("data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB")
        assertEquals("tok2", api(h).loginStep2("alice", "s-1", frames))
        assertEquals("http://10.0.2.2:8000/login/step2", h.url)
        val body = JSONObject(h.body!!)
        assertEquals("alice", body.getString("username"))
        assertEquals("s-1", body.getString("session_id"))
        assertEquals("android_abc123", body.getString("device_identifier"))
        val images = body.getJSONArray("images")
        assertEquals(2, images.length())
        assertEquals("data:image/jpeg;base64,BBB", images.getString(1))
    }

    @Test fun enrollSendsTheFacePhoto() {
        val h = FakeHttp(HttpResult(200, """{"access_token":"t","token_type":"bearer"}"""))
        assertEquals("t", api(h).enroll("bob", "secret", "data:image/jpeg;base64,XYZ"))
        assertEquals("http://10.0.2.2:8000/enroll", h.url)
        val body = JSONObject(h.body!!)
        assertEquals("bob", body.getString("username"))
        assertEquals("data:image/jpeg;base64,XYZ", body.getString("face_image_b64"))
    }

    @Test fun historyUsesTheBearerTokenAndParses() {
        val h = FakeHttp(HttpResult(200, """[
            {"id":2,"timestamp":"2026-11-14T10:05:33.123456","device_identifier":"android_abc123","risk_score":"LOW","face_check_triggered":false,"success":true},
            {"id":1,"timestamp":"2026-11-14T09:00:00","device_identifier":"android_abc123","risk_score":"HIGH","face_check_triggered":true,"success":false}]"""))
        val items = api(h).history("tok")
        assertEquals("GET", h.method)
        assertEquals("http://10.0.2.2:8000/history", h.url)
        assertEquals("Bearer tok", h.headers["Authorization"])
        assertEquals(2, items.size)
        assertTrue(items[0].success); assertFalse(items[0].faceCheckTriggered)
        assertEquals("HIGH", items[1].riskScore); assertTrue(items[1].faceCheckTriggered); assertFalse(items[1].success)
    }

    @Test fun serverErrorDetailIsShown() {
        val h = FakeHttp(HttpResult(401, """{"detail":"Incorrect username or password"}"""))
        try {
            api(h).loginStep1("alice", "wrong"); fail()
        } catch (e: ApiException) {
            assertEquals(401, e.status); assertEquals("Incorrect username or password", e.message)
        }
    }

    @Test fun unreachableServerGivesAHelpfulMessage() {
        val h = FakeHttp(fail = IOException("connect failed"))
        try {
            api(h).ping(); fail()
        } catch (e: ApiException) {
            assertEquals(0, e.status); assertTrue(e.message.contains("http://10.0.2.2:8000/"))
        }
    }

    @Test fun validationErrorsAreJoined() {
        val e = parseError(422, """{"detail":[{"msg":"field required"},{"msg":"too long"}]}""")
        assertEquals("field required; too long", e.message)
    }

    @Test fun nonJsonErrorFallsBack() {
        assertEquals("Request failed (502)", parseError(502, "<html>Bad gateway</html>").message)
    }
}
