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
private val NO_CHECK = HttpResult(201, """{"action":{"id":"ACT1","kind":"transfer","status":"AWAITING_VERIFICATION","amountMinor":50000,"checks":[],"payee":{"display":"V. Mehta","initials":"VM"}},"challengeUrl":null}""")
private val FACE_CHECK = HttpResult(201, """{"action":{"id":"ACT1","kind":"transfer","status":"AWAITING_VERIFICATION","amountMinor":500000,"checks":["face"],"reason":"a larger amount than usual","payee":{"display":"V. Mehta","initials":"VM"}},"challengeUrl":"$CHALLENGE_URL"}""")
private const val CH_FACE = """{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"FACE","params":{"action":"TURN_LEFT","frames":20,"intervalMs":150}}"""
private fun session(status: String, challenge: String?) = """{"status":"$status","approving":{"amount":"₹5,000.00","payee":"V. Mehta"},"steps":[{"factor":"FACE","status":"PENDING"}],"challenge":${challenge ?: "null"}}"""
private fun submitted(factorStatus: String, retry: Boolean, guidance: String?, state: String) =
    ok("""{"result":{"factor":"FACE","status":"$factorStatus","retry":$retry,"guidance":${guidance?.let { "\"$it\"" } ?: "null"}},"escalation":null,"state":$state}""")
private fun done(status: String, detail: String? = null) =
    ok("""{"action":{"id":"ACT1","kind":"transfer","status":"$status","amountMinor":500000,"payee":{"display":"V. Mehta"},"detail":${detail?.let { "\"$it\"" } ?: "null"}},"balance":49500000}""")
private val FRAMES20 = List(20) { "data:image/jpeg;base64,AAAA" }
private val FRAMES5 = List(5) { "data:image/jpeg;base64,AAAA" }

@OptIn(ExperimentalCoroutinesApi::class)
class AppViewModelTest {
    private val dispatcher = UnconfinedTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm(http: RoutedHttp, settings: InMemorySettings = InMemorySettings()) =
        AppViewModel(settings, BankApi(http, { settings.baseUrl }, { settings.deviceId }), { link -> AmfaApi(http, link) }, dispatcher)

    private fun signedIn(http: RoutedHttp): AppViewModel = vm(http).also { it.signIn("alice", "pw") }

    @Test fun startsOnTheWelcomeScreen() {
        assertEquals(Screen.Welcome, vm(RoutedHttp()).screen)
    }

    @Test fun loginOpensTheHomeScreenWithBalanceAndActivity() {
        val s = InMemorySettings()
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY)
        val vm = vm(http, s)
        vm.signIn("  Alice ", "pw")
        assertEquals(Screen.Home, vm.screen)
        assertEquals("alice", JSONObject(http.last("/api/login").body!!).getString("username"))
        assertEquals("alice", s.username)
        assertEquals(50_000_000L, vm.me!!.balanceMinor)
        assertEquals(1, vm.activity.size)
        assertFalse(vm.busy)
    }

    @Test fun wrongPasswordStaysOnSignInWithTheBanksMessage() {
        val vm = vm(RoutedHttp("POST /api/login" to HttpResult(401, """{"detail":"wrong username or password"}""")))
        vm.go(Screen.SignIn)
        vm.signIn("alice", "nope")
        assertEquals(Screen.SignIn, vm.screen)
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
        vm.signUp("Alice", "Alice Rao", "+919812345678", "a@example.com", "longenough1")
        assertEquals(Screen.Setup, vm.screen)
        vm.startFaceSetup()
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
        assertEquals(Screen.Home, vm.screen)
        assertTrue(vm.notice!!.contains("all set"))
    }

    @Test fun aRejectedFaceSetupStaysOnTheScreenWithAReason() {
        val http = RoutedHttp(
            "POST /api/register" to HttpResult(201, "{}"), "GET /api/me" to ME_NO_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/amfa/enrol" to ok("""{"url":"http://127.0.0.1:8040/hosted/enroll#token=t"}"""),
            "GET /c/v1/enrollment" to ok("""{"consent":{"required":false,"version":"v"},"enrolled":{"FACE":false}}"""),
            "POST /c/v1/enrollment/face" to ok("""{"enrolled":false,"factor":"FACE","reason":"LIVENESS_NOT_ESTABLISHED"}"""),
        )
        val vm = vm(http)
        vm.signUp("alice", "Alice Rao", "+919812345678", "a@example.com", "longenough1")
        vm.startFaceSetup()
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
        vm.startPayment("transfer", "BEN-42", 500.0)
        assertTrue(vm.screen is Screen.Review)
        vm.confirmPayment()
        assertEquals("ok", (vm.screen as Screen.Result).outcome.kind)
        assertNull(vm.error)
    }

    @Test fun aChallengedPaymentGoesThroughTheFaceCheckAndIsThenSettledByTheBank() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /c/v1/session/factors/FACE" to submitted("PASSED", false, null, session("VERIFIED", null)),
            "POST /api/actions/ACT1/complete" to done("DONE"))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0)
        assertEquals(listOf("face"), (vm.screen as Screen.Review).started.action.checks)
        vm.confirmPayment()
        val v = vm.screen as Screen.Verify
        assertEquals("TURN_LEFT", v.session.challenge!!.action)
        assertEquals("Bearer sess-tok", http.last("/c/v1/session").headers["Authorization"])
        assertTrue(http.last("/c/v1/session").url.startsWith("http://10.0.2.2:8040/"))

        vm.submitFace(FRAMES20)
        val sent = JSONObject(http.last("/factors/FACE").body!!)
        assertEquals("ch_aaaaaaaaaaaaaaaaaaaa", sent.getString("challengeId"))
        assertEquals(20, sent.getJSONObject("payload").getJSONArray("frames").length())
        assertTrue(http.called("/api/actions/ACT1/complete"))
        val r = vm.screen as Screen.Result
        assertEquals("ok", r.outcome.kind)
        assertTrue(r.outcome.title.endsWith("sent"))
    }

    @Test fun aFailedFaceCheckAllowsAnotherTryWithTheServersGuidance() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /c/v1/session/factors/FACE" to submitted("FAILED", true, "Face a light and try again", session("PENDING", CH_FACE)))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0)
        vm.confirmPayment()
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
        vm.startPayment("transfer", "BEN-42", 5000.0)
        vm.confirmPayment()
        vm.submitFace(FRAMES20)
        val r = vm.screen as Screen.Result
        assertEquals("bad", r.outcome.kind)
        assertTrue(r.outcome.text.contains("blocked after repeated failed checks"))
    }

    @Test fun aCodeCheckSendsTheSixDigits() {
        val ch = """{"challengeId":"ch_bbbbbbbbbbbbbbbbbbbb","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com"}}"""
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", ch)),
            "POST /c/v1/session/factors/OTP" to submitted("PASSED", false, null, session("VERIFIED", null)),
            "POST /api/actions/ACT1/complete" to done("DONE"))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0)
        vm.confirmPayment()
        vm.submitCode(" 123456 ")
        assertEquals("123456", JSONObject(http.last("/factors/OTP").body!!).getJSONObject("payload").getString("code"))
        assertTrue(vm.screen is Screen.Result)
    }

    @Test fun cancellingAPaymentTellsTheBankAndReturnsHome() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", CH_FACE)),
            "POST /api/actions/ACT1/cancel" to done("CANCELLED"))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0)
        vm.confirmPayment()
        vm.back()
        assertTrue(http.called("/api/actions/ACT1/cancel"))
        assertEquals(Screen.Home, vm.screen)
        assertTrue(vm.notice!!.contains("cancelled"))
    }

    @Test fun anExpiredBankSessionSignsTheCustomerOut() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY)
        val vm = signedIn(http)
        http.routes["GET /api/me"] = HttpResult(401, """{"detail":"sign in to the bank first"}""")
        vm.refreshHome()
        assertEquals(Screen.SignIn, vm.screen)
        assertTrue(vm.error!!.contains("expired"))
        assertNull(vm.me)
    }

    @Test fun signingOutClearsTheHomeData() {
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/logout" to ok("""{"signedIn":false}"""))
        val vm = signedIn(http)
        vm.logout()
        assertEquals(Screen.SignIn, vm.screen)
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
        vm.go(Screen.SignUp)
        vm.go(Screen.Settings)
        vm.back()
        assertEquals(Screen.SignUp, vm.screen)
    }

    // ---- voice and typing

    private fun setupHttp(factor: String, state: String, vararg more: Pair<String, HttpResult>) = RoutedHttp(
        "POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
        "POST /api/amfa/enrol" to ok("""{"url":"http://127.0.0.1:8040/hosted/enroll#token=$factor-tok"}"""),
        "GET /c/v1/enrollment" to ok(state), "POST /c/v1/enrollment/consent" to ok("""{"consented":true}"""),
        "POST /c/v1/enrollment/complete" to ok("""{"complete":true}"""), *more)

    @Test fun voiceSetupSendsThreeClipsAfterConsent() {
        val http = setupHttp("voice", """{"consent":{"required":true,"version":"v1"},"enrolled":{"VOICE":false}}""",
            "POST /c/v1/enrollment/voice" to ok("""{"enrolled":true,"factor":"VOICE"}"""))
        val vm = signedIn(http)
        vm.startSetup("VOICE")
        assertTrue(vm.screen is Screen.VoiceSetup)
        assertEquals("VOICE", JSONObject(http.last("/api/amfa/enrol").body!!).getJSONArray("factors").getString(0))
        vm.submitVoiceSetup(listOf("w1", "w2", "w3"))
        assertTrue(http.called("/enrollment/consent"))
        assertEquals(3, JSONObject(http.last("/enrollment/voice").body!!).getJSONArray("samples").length())
        assertEquals(Screen.Home, vm.screen)
        assertTrue(vm.notice!!.startsWith("Voice"))
    }

    @Test fun aRejectedVoiceSetupStaysAndExplains() {
        val http = setupHttp("voice", """{"consent":{"required":false,"version":"v1"},"enrolled":{"VOICE":false}}""",
            "POST /c/v1/enrollment/voice" to ok("""{"enrolled":false,"reason":"TOO_NOISY"}"""))
        val vm = signedIn(http)
        vm.startSetup("VOICE")
        vm.submitVoiceSetup(listOf("w1", "w2", "w3"))
        assertTrue(vm.screen is Screen.VoiceSetup)
        assertTrue(vm.error!!.contains("quieter"))
        assertFalse(http.called("/enrollment/complete"))
    }

    @Test fun typingSetupSendsSamplesAndTraces() {
        val http = setupHttp("ueba", """{"consent":{"required":false,"version":"v1"},"enrolled":{"UEBA":false},
            "behaviour":{"phrase":"pack my box","repetitions":2,"shape":[[0.1,0.5],[0.9,0.5]],"shapeTracesRequired":1}}""",
            "POST /c/v1/enrollment/behaviour" to ok("""{"enrolled":true,"factor":"UEBA","quality":{}}"""))
        val vm = signedIn(http)
        vm.startSetup("UEBA")
        val s = vm.screen as Screen.TypingSetup
        assertEquals("pack my box", s.behaviour.phrase)
        val sample = org.json.JSONArray().put(JSONObject().put("key", "p").put("down", 1.0).put("up", 4.0))
        vm.submitTypingSetup(listOf(sample, sample), listOf(listOf(com.adaptivemfa.app.logic.TracePoint(0.1, 0.5, 1.0))))
        val body = JSONObject(http.last("/enrollment/behaviour").body!!)
        assertEquals("touch", body.getString("input"))
        assertEquals(2, body.getJSONArray("keystrokeSamples").length())
        assertEquals(1, body.getJSONArray("shapeTraces").length())
        assertEquals(Screen.Home, vm.screen)
    }

    private fun checkHttp(ch: String, factor: String, submit: HttpResult) = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
        "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", ch)),
        "POST /c/v1/session/factors/$factor" to submit, "POST /api/actions/ACT1/complete" to done("DONE"))

    @Test fun aVoiceCheckSendsTheRecordingAsAudio() {
        val ch = """{"challengeId":"ch_dddddddddddddddddddd","factor":"VOICE","params":{"prompt":"river maple orbit 1 2 3 4","minSeconds":3,"maxSeconds":10}}"""
        val http = checkHttp(ch, "VOICE", submitted("PASSED", false, null, session("VERIFIED", null)))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0); vm.confirmPayment()
        assertEquals("river maple orbit 1 2 3 4", (vm.screen as Screen.Verify).session.challenge!!.prompt)
        vm.submitVoice("UklGRg==")
        assertEquals("UklGRg==", JSONObject(http.last("/factors/VOICE").body!!).getJSONObject("payload").getString("audio"))
        assertEquals("ok", (vm.screen as Screen.Result).outcome.kind)
    }

    @Test fun aTypingCheckSendsTheSampleAndTheTraceAsTouch() {
        val ch = """{"challengeId":"ch_eeeeeeeeeeeeeeeeeeee","factor":"UEBA","params":{"phrase":"pack my box","input":"touch","shape":[[0.1,0.5],[0.9,0.5]]}}"""
        val http = checkHttp(ch, "UEBA", submitted("PASSED", false, null, session("VERIFIED", null)))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0); vm.confirmPayment()
        val sample = org.json.JSONArray().put(JSONObject().put("key", "p").put("down", 1.0).put("up", 4.0))
        vm.submitTyping(sample, listOf(com.adaptivemfa.app.logic.TracePoint(0.1, 0.5, 2.0), com.adaptivemfa.app.logic.TracePoint(0.5, 0.5, 9.0)))
        val p = JSONObject(http.last("/factors/UEBA").body!!).getJSONObject("payload")
        assertEquals("touch", p.getString("keystrokeInput"))
        assertEquals("touch", p.getString("shapePointerType"))
        assertEquals(2, p.getJSONArray("shapeTrace").length())
        assertEquals(1, p.getJSONArray("keystrokeSample").length())
    }

    @Test fun aTypingCheckWithoutAShapeSendsNoTrace() {
        val ch = """{"challengeId":"ch_ffffffffffffffffffff","factor":"UEBA","params":{"phrase":"pack my box","input":"touch","shape":null}}"""
        val http = checkHttp(ch, "UEBA", submitted("PASSED", false, null, session("VERIFIED", null)))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0); vm.confirmPayment()
        vm.submitTyping(org.json.JSONArray(), null)
        assertFalse(JSONObject(http.last("/factors/UEBA").body!!).getJSONObject("payload").has("shapeTrace"))
    }

    // ---- authenticator app and resend

    @Test fun authenticatorSetupShowsTheSecretThenConfirmsAndCompletes() {
        val http = setupHttp("otp", """{"consent":{"required":false,"version":"v1"},"enrolled":{"OTP":false},"otpMethods":["authenticator"]}""",
            "POST /c/v1/enrollment/totp/start" to ok("""{"otpauthUri":"otpauth://totp/Bank:me?secret=ABCD2345","qrSvg":"","secret":"ABCD2345"}"""),
            "POST /c/v1/enrollment/totp/confirm" to ok("""{"enrolled":true,"factor":"OTP"}"""))
        val vm = signedIn(http)
        vm.startSetup("OTP")
        assertEquals("ABCD2345", (vm.screen as Screen.AuthenticatorSetup).secret)
        assertEquals("OTP", JSONObject(http.last("/api/amfa/enrol").body!!).getJSONArray("factors").getString(0))
        vm.confirmAuthenticator(" 123456 ")
        assertEquals("123456", JSONObject(http.last("/totp/confirm").body!!).getString("code"))
        assertTrue(http.called("/enrollment/complete"))
        assertEquals(Screen.Home, vm.screen)
        assertTrue(vm.notice!!.startsWith("Authenticator app set up"))
    }

    @Test fun aWrongAuthenticatorCodeStaysOnTheSetupScreen() {
        val http = setupHttp("otp", """{"consent":{"required":false,"version":"v1"},"enrolled":{"OTP":false}}""",
            "POST /c/v1/enrollment/totp/start" to ok("""{"otpauthUri":"otpauth://totp/x","qrSvg":"","secret":"ABCD2345"}"""),
            "POST /c/v1/enrollment/totp/confirm" to HttpResult(400, """{"error":{"code":"OTP_INVALID","message":"that code is not valid"}}"""))
        val vm = signedIn(http)
        vm.startSetup("OTP")
        vm.confirmAuthenticator("000000")
        assertTrue(vm.screen is Screen.AuthenticatorSetup)
        assertEquals("that code is not valid", vm.error)
        assertFalse(http.called("/enrollment/complete"))
    }

    @Test fun resendingAnEmailedCodeSwapsInTheNewChallenge() {
        val first = """{"challengeId":"ch_bbbbbbbbbbbbbbbbbbbb","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com"}}"""
        val second = """{"challengeId":"ch_cccccccccccccccccccc","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com"}}"""
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", first)),
            "POST /c/v1/session/otp/resend" to ok(session("PENDING", second)))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0); vm.confirmPayment()
        vm.resendCode()
        assertEquals("ch_cccccccccccccccccccc", (vm.screen as Screen.Verify).session.challenge!!.id)
        assertEquals("We sent a new code.", vm.notice)
    }

    @Test fun resendingTooSoonShowsTheServersWaitMessage() {
        val ch = """{"challengeId":"ch_bbbbbbbbbbbbbbbbbbbb","factor":"OTP","params":{"mode":"delivered","sentTo":"a***@example.com"}}"""
        val http = RoutedHttp("POST /api/login" to LOGIN, "GET /api/me" to ME_FACE, "GET /api/activity" to ACTIVITY,
            "POST /api/actions/transfer" to FACE_CHECK, "GET /c/v1/session" to ok(session("PENDING", ch)),
            "POST /c/v1/session/otp/resend" to HttpResult(429, """{"error":{"code":"OTP_RESEND_WAIT","message":"wait a few seconds before asking for another code"}}"""))
        val vm = signedIn(http)
        vm.startPayment("transfer", "BEN-42", 5000.0); vm.confirmPayment()
        vm.resendCode()
        assertTrue(vm.screen is Screen.Verify)
        assertEquals("wait a few seconds before asking for another code", vm.error)
    }
}
