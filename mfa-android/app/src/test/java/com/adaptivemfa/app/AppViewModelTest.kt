package com.adaptivemfa.app

import com.adaptivemfa.app.data.Api
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

private val HISTORY = HttpResult(200, """[{"id":1,"timestamp":"2026-11-14T10:00:00","device_identifier":"android_test01","risk_score":"LOW","face_check_triggered":false,"success":true}]""")
private val LOW = HttpResult(200, """{"require_step2":false,"access_token":"tok-low","token_type":"bearer","risk_score":"LOW"}""")
private val HIGH = HttpResult(200, """{"require_step2":true,"risk_score":"HIGH","session_id":"sess-1","challenge_type":"blink"}""")
private val FRAMES = List(13) { "data:image/jpeg;base64,AAAA" }

@OptIn(ExperimentalCoroutinesApi::class)
class AppViewModelTest {
    private val dispatcher = UnconfinedTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm(http: RoutedHttp, settings: InMemorySettings = InMemorySettings()) =
        AppViewModel(settings, Api(http, { settings.baseUrl }, { settings.deviceId }), dispatcher)

    @Test fun startsOnLoginWithoutAToken() {
        assertEquals(Screen.Login, vm(RoutedHttp()).screen)
    }

    @Test fun startsOnDashboardWhenAlreadySignedIn() {
        assertEquals(Screen.Dashboard, vm(RoutedHttp(), InMemorySettings(token = "t", username = "alice")).screen)
    }

    @Test fun knownDeviceLogsInWithPasswordOnly() {
        val s = InMemorySettings()
        val http = RoutedHttp("POST /login/step1" to LOW, "GET /history" to HISTORY)
        val vm = vm(http, s)
        vm.login("  alice ", "pw")
        assertEquals(Screen.Dashboard, vm.screen)
        assertEquals("tok-low", s.token)
        assertEquals("alice", s.username)
        assertEquals("alice", JSONObject(http.last("/login/step1").body!!).getString("username"))
        assertEquals("Bearer tok-low", http.last("/history").headers["Authorization"])
        assertEquals(1, vm.history.size)
        assertTrue(vm.notice!!.contains("LOW"))
        assertFalse(vm.busy)
    }

    @Test fun riskyLoginOpensTheFaceCheck() {
        val s = InMemorySettings()
        val vm = vm(RoutedHttp("POST /login/step1" to HIGH), s)
        vm.login("alice", "pw")
        assertEquals(Screen.FaceCheck("alice", "sess-1", "blink", "HIGH"), vm.screen)
        assertNull(s.token)
    }

    @Test fun passedFaceCheckSignsIn() {
        val s = InMemorySettings()
        val http = RoutedHttp(
            "POST /login/step1" to HIGH,
            "POST /login/step2" to HttpResult(200, """{"access_token":"tok-face","token_type":"bearer"}"""),
            "GET /history" to HISTORY,
        )
        val vm = vm(http, s)
        vm.login("alice", "pw")
        vm.submitFaceCheck(FRAMES)
        assertEquals(Screen.Dashboard, vm.screen)
        assertEquals("tok-face", s.token)
        val body = JSONObject(http.last("/login/step2").body!!)
        assertEquals("sess-1", body.getString("session_id"))
        assertEquals("android_test01", body.getString("device_identifier"))
        assertEquals(13, body.getJSONArray("images").length())
    }

    @Test fun failedFaceCheckReturnsToLoginBecauseTheSessionIsUsedUp() {
        val s = InMemorySettings()
        val http = RoutedHttp(
            "POST /login/step1" to HIGH,
            "POST /login/step2" to HttpResult(401, """{"detail":"Verification failed. Please try again."}"""),
        )
        val vm = vm(http, s)
        vm.login("alice", "pw")
        vm.submitFaceCheck(FRAMES)
        assertEquals(Screen.Login, vm.screen)
        assertTrue(vm.error!!.startsWith("Verification failed."))
        assertTrue(vm.error!!.contains("Log in again"))
        assertNull(s.token)
        assertFalse(vm.busy)
    }

    @Test fun faceCheckIsIgnoredOutsideTheFaceScreen() {
        val http = RoutedHttp()
        vm(http).submitFaceCheck(FRAMES)
        assertTrue(http.calls.isEmpty())
    }

    @Test fun wrongPasswordStaysOnLoginWithTheServerMessage() {
        val vm = vm(RoutedHttp("POST /login/step1" to HttpResult(401, """{"detail":"Incorrect username or password"}""")))
        vm.login("alice", "nope")
        assertEquals(Screen.Login, vm.screen)
        assertEquals("Incorrect username or password", vm.error)
    }

    @Test fun enrollSignsInAndSendsThePhoto() {
        val s = InMemorySettings()
        val http = RoutedHttp("POST /enroll" to HttpResult(200, """{"access_token":"tok-new","token_type":"bearer"}"""), "GET /history" to HttpResult(200, "[]"))
        val vm = vm(http, s)
        vm.go(Screen.Enroll)
        vm.enroll("bob", "secret", "data:image/jpeg;base64,XYZ")
        assertEquals(Screen.Dashboard, vm.screen)
        assertEquals("tok-new", s.token)
        assertEquals("data:image/jpeg;base64,XYZ", JSONObject(http.last("/enroll").body!!).getString("face_image_b64"))
    }

    @Test fun enrollErrorStaysOnEnroll() {
        val vm = vm(RoutedHttp("POST /enroll" to HttpResult(400, """{"detail":"Username already registered"}""")))
        vm.go(Screen.Enroll)
        vm.enroll("bob", "secret", "data:image/jpeg;base64,XYZ")
        assertEquals(Screen.Enroll, vm.screen)
        assertEquals("Username already registered", vm.error)
    }

    @Test fun expiredTokenLogsOut() {
        val s = InMemorySettings(token = "old", username = "alice")
        val vm = vm(RoutedHttp("GET /history" to HttpResult(401, """{"detail":"Could not validate credentials"}""")), s)
        vm.refreshHistory()
        assertEquals(Screen.Login, vm.screen)
        assertNull(s.token)
        assertTrue(vm.error!!.contains("expired"))
    }

    @Test fun logoutClearsTheTokenButKeepsTheUsername() {
        val s = InMemorySettings(token = "t", username = "alice")
        val vm = vm(RoutedHttp(), s)
        vm.logout()
        assertEquals(Screen.Login, vm.screen)
        assertNull(s.token)
        assertEquals("alice", vm.username)
    }

    @Test fun badServerAddressIsRejected() {
        val s = InMemorySettings()
        val vm = vm(RoutedHttp(), s)
        assertFalse(vm.saveBaseUrl("not an address"))
        assertTrue(vm.error!!.contains("server address"))
        assertEquals("http://10.0.2.2:8000", s.baseUrl)
        assertTrue(vm.saveBaseUrl("192.168.1.20:8000/"))
        assertEquals("http://192.168.1.20:8000", s.baseUrl)
    }

    @Test fun connectionTestReportsSuccessAndFailure() {
        val http = RoutedHttp("GET /openapi.json" to HttpResult(200, "{}"))
        val vm = vm(http)
        vm.testConnection("10.0.2.2:8000")
        assertEquals("Connected to http://10.0.2.2:8000", vm.notice)
        http.routes.clear()
        vm.testConnection("10.0.2.2:8000")
        assertTrue(vm.error!!.startsWith("Cannot reach the server"))
    }

    @Test fun settingsBackReturnsToWhereYouCameFrom() {
        val vm = vm(RoutedHttp(), InMemorySettings(token = "t", username = "alice"))
        vm.go(Screen.Settings)
        vm.back()
        assertEquals(Screen.Dashboard, vm.screen)
        vm.logout()
        vm.go(Screen.Enroll)
        vm.back()
        assertEquals(Screen.Login, vm.screen)
    }
}
