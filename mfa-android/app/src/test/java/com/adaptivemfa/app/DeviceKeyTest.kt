package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.AmfaLink
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.DeviceAuthRequired
import com.adaptivemfa.app.data.DeviceKeyInvalid
import com.adaptivemfa.app.data.DeviceKeys
import com.adaptivemfa.app.data.DeviceSigner
import com.adaptivemfa.app.data.E2E
import com.adaptivemfa.app.ui.AppViewModel
import com.adaptivemfa.app.ui.Screen
import com.adaptivemfa.app.ui.learningProgress
import com.adaptivemfa.app.ui.learningText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec

/** A phone's secure-hardware key, played by a software key; `locked` is the lock screen not yet confirmed. */
class FakeSigner(var available: Boolean = true, var locked: Boolean = false, var invalid: Boolean = false) : DeviceSigner {
    var pair: KeyPair? = null
    var signed = mutableListOf<String>()
    override fun available() = available
    override fun hasKey() = pair != null
    override fun createKey(): JSONObject {
        pair = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        return DeviceKeys.jwk(pair!!.public as ECPublicKey)
    }
    override fun publicJwk(): JSONObject? = pair?.let { DeviceKeys.jwk(it.public as ECPublicKey) }
    override fun sign(message: String): String {
        if (invalid) throw DeviceKeyInvalid()
        if (locked) throw DeviceAuthRequired()
        signed += message
        return E2E.b64e(Signature.getInstance("SHA256withECDSA").apply { initSign(pair!!.private); update(message.toByteArray()) }.sign())
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class DeviceKeyTest {
    private val dispatcher = UnconfinedTestDispatcher()
    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private val ENROL_LINK = ok("""{"url":"http://127.0.0.1:8040/hosted/enroll#token=enr-tok"}""")
    private val ME = ok("""{"name":"Alice Rao","balance":50000000,"enrolled":null,"protection":{"level":"Basic"},"payees":[{"ref":"BEN-42","display":"V. Mehta"}],
        "learning":{"enabled":true,"phase":"LEARNING","verifiedPayments":3,"offerBiometrics":false,"progress":{"payments":3,"moreAtLeast":9,"missing":["proven"]}}}""")
    private val CHALLENGE_URL = "http://127.0.0.1:8040/hosted/challenge?t=demo-bank&embed=1#token=sess-tok"
    private val DEVICE_STARTED = HttpResultOf("""{"action":{"id":"ACT1","kind":"transfer","status":"AWAITING_VERIFICATION","amountMinor":150000,"checks":["fingerprint or screen lock"],"payee":{"display":"V. Mehta","initials":"VM"}},"challengeUrl":"$CHALLENGE_URL"}""")
    private val SESSION = ok("""{"sessionId":"sess_1","status":"PENDING","approving":{"amount":"₹1,500.00","payee":"V. Mehta"},"steps":[{"factor":"DEVICE","status":"PENDING"}],
        "challenge":{"challengeId":"ch_aaaaaaaaaaaaaaaaaaaa","factor":"DEVICE","params":{"message":"amfa-device|sess_1|n0nce","alg":"ES256"}}}""")
    private val PASSED = ok("""{"result":{"factor":"DEVICE","status":"PASSED","retry":false},"state":{"sessionId":"sess_1","status":"VERIFIED","steps":[]}}""")
    private val DONE = ok("""{"action":{"id":"ACT1","kind":"transfer","status":"DONE","amountMinor":150000,"payee":{"display":"V. Mehta"}},"balance":49850000}""")
    private fun HttpResultOf(b: String) = com.adaptivemfa.app.data.HttpResult(201, b)

    private fun routes() = RoutedHttp("POST /api/login" to ok("""{"signedIn":true}"""), "GET /api/me" to ME, "GET /api/activity" to ok("""{"activity":[]}"""),
        "POST /api/amfa/enrol" to ENROL_LINK, "POST /c/v1/enrollment/device" to ok("""{"bound":true}"""), "POST /c/v1/enrollment/complete" to ok("""{"complete":true}"""),
        "POST /api/actions/transfer" to DEVICE_STARTED, "GET /c/v1/session" to SESSION, "POST /c/v1/session/factors/DEVICE" to PASSED,
        "POST /api/actions/ACT1/complete" to DONE)

    private fun vm(http: RoutedHttp, signer: DeviceSigner, settings: InMemorySettings = InMemorySettings(deviceId = "android_0123456789ab")) =
        AppViewModel(settings, BankApi(http, { settings.baseUrl }, { settings.deviceId }), { link -> AmfaApi(http, link, secure = true) }, dispatcher, signer)

    // ---- the key and what AMFA reads of it

    private val params: ECParameterSpec = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }.getParameterSpec(ECParameterSpec::class.java)

    @Test fun theKeyGoesToAmfaAsAP256JwkAndTheSignatureVerifiesAgainstIt() {
        val s = FakeSigner(); val jwk = s.createKey()
        assertEquals("EC", jwk.getString("kty")); assertEquals("P-256", jwk.getString("crv"))
        val x = E2E.b64d(jwk.getString("x")); val y = E2E.b64d(jwk.getString("y"))
        assertEquals(32, x.size); assertEquals(32, y.size)
        val rebuilt = KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(ECPoint(BigInteger(1, x), BigInteger(1, y)), params))
        val sig = E2E.b64d(s.sign("amfa-device|sess_1|abc"))
        assertTrue(Signature.getInstance("SHA256withECDSA").apply { initVerify(rebuilt); update("amfa-device|sess_1|abc".toByteArray()) }.verify(sig))
        assertFalse(Signature.getInstance("SHA256withECDSA").apply { initVerify(rebuilt); update("amfa-device|sess_1|other".toByteArray()) }.verify(sig))
    }

    // ---- giving AMFA the key

    @Test fun afterSignInTheKeyIsMadeAndHandedToAmfaOnce() {
        val http = routes(); val signer = FakeSigner(); val settings = InMemorySettings(deviceId = "android_0123456789ab")
        val v = vm(http, signer, settings)
        v.signIn("alice", "pw")
        val sent = JSONObject(http.last("/enrollment/device").body!!)
        assertEquals("android_0123456789ab", sent.getString("deviceId")); assertEquals("P-256", sent.getJSONObject("publicJwk").getString("crv"))
        assertFalse(sent.getJSONObject("publicJwk").has("d"))                                    // only the public half leaves the phone
        assertEquals("enr-tok", http.last("/enrollment/device").headers["Authorization"]!!.removePrefix("Bearer "))
        assertTrue(http.called("/enrollment/complete")); assertNotNull(settings.deviceKeyFor)
        val before = http.calls.size
        vm(http, signer, settings).signIn("alice", "pw")
        assertEquals(0, http.calls.drop(before).count { it.url.endsWith("/enrollment/device") })   // already done for this account
    }

    /** A real bank only reports the learning state once AMFA knows the customer, which is when the phone's key is handed over. */
    private class BankThatKnowsAfterEnrolment(val inner: RoutedHttp, val learning: com.adaptivemfa.app.data.HttpResult) : com.adaptivemfa.app.data.Http {
        override fun request(method: String, url: String, headers: Map<String, String>, body: String?): com.adaptivemfa.app.data.HttpResult {
            val r = inner.request(method, url, headers, body)
            if (url.endsWith("/c/v1/enrollment/complete")) inner.routes["GET /api/me"] = learning
            return r
        }
    }

    @Test fun openingAnAccountAtABankThatLearnsItsCustomersSkipsFaceSetupAndStillGivesAmfaTheKey() {
        val inner = routes(); inner.routes["POST /api/register"] = ok("""{"registered":true}""")
        val learning = inner.routes["GET /api/me"]!!
        inner.routes["GET /api/me"] = ok("""{"name":"A","balance":1,"enrolled":null,"protection":{"level":"Basic"},"payees":[]}""")   // before AMFA knows the customer
        val http = BankThatKnowsAfterEnrolment(inner, learning); val signer = FakeSigner(); val settings = InMemorySettings(deviceId = "android_0123456789ab")
        val v = AppViewModel(settings, BankApi(http, { settings.baseUrl }, { settings.deviceId }), { link -> AmfaApi(http, link, secure = true) }, dispatcher, signer)
        v.signUp("riyapatel1", "Riya Patel", "9812345678", "riya@example.com", "long-password-1")
        assertEquals(Screen.Home, v.screen)                                   // not the "Let's protect your account" face step
        assertTrue(inner.called("/enrollment/device")); assertNotNull(signer.pair)
    }

    @Test fun aBankThatDoesNotLearnItsCustomersKeepsTheSetupStep() {
        val http = routes(); http.routes["POST /api/register"] = ok("""{"registered":true}"""); http.routes["GET /api/me"] = ok("""{"name":"A","balance":1,"enrolled":null,"protection":{"level":"Basic"},"payees":[]}""")
        val v = vm(http, FakeSigner())
        v.signUp("riyapatel1", "Riya Patel", "9812345678", "riya@example.com", "long-password-1")
        assertEquals(Screen.Setup, v.screen)
    }

    @Test fun aPhoneWithNoScreenLockMakesNoKeyAndKeepsUsingCodes() {
        val http = routes(); val signer = FakeSigner(available = false)
        vm(http, signer).signIn("alice", "pw")
        assertNull(signer.pair); assertFalse(http.called("/enrollment/device"))
    }

    @Test fun aFailedEnrolmentNeverBreaksSigningIn() {
        val http = routes(); http.routes["POST /c/v1/enrollment/device"] = com.adaptivemfa.app.data.HttpResult(409, """{"error":{"code":"DEVICE_BINDING_REQUIRES_REPLACE","message":"x"}}""")
        val v = vm(http, FakeSigner(), InMemorySettings(deviceId = "android_0123456789ab"))
        v.signIn("alice", "pw")
        assertEquals(Screen.Home, v.screen); assertNull(v.error)
    }

    // ---- confirming a payment with it

    private fun startedPayment(signer: FakeSigner): AppViewModel {
        val http = routes(); val v = vm(http, signer).also { it.signIn("alice", "pw") }
        v.startPayment("transfer", "BEN-42", 1500.0); v.confirmPayment()
        return v
    }

    @Test fun theDeviceChallengeIsSignedAndSentAsItIsNotInABox() {
        val http = routes(); val signer = FakeSigner()
        val v = vm(http, signer).also { it.signIn("alice", "pw") }
        v.startPayment("transfer", "BEN-42", 1500.0); v.confirmPayment()
        assertTrue(v.screen is Screen.Verify)
        v.signDevice()
        val body = JSONObject(http.last("/session/factors/DEVICE").body!!)
        assertEquals("ch_aaaaaaaaaaaaaaaaaaaa", body.getString("challengeId"))
        assertTrue(body.getJSONObject("payload").has("signature")); assertFalse(body.getJSONObject("payload").has("e2e"))
        assertEquals(listOf("amfa-device|sess_1|n0nce"), signer.signed)
        assertFalse(http.called("/e2e-key"))                                                     // nothing biometric: no encryption key needed
        assertTrue(v.screen is Screen.Result)
    }

    @Test fun untilTheLockScreenIsConfirmedNothingIsSentAndThenItIs() {
        val http = routes(); val signer = FakeSigner(locked = true)
        val v = vm(http, signer).also { it.signIn("alice", "pw") }
        v.startPayment("transfer", "BEN-42", 1500.0); v.confirmPayment()
        v.signDevice()
        assertTrue(v.deviceUnlock); assertFalse(http.called("/session/factors/DEVICE"))
        signer.locked = false                                                                    // the customer confirmed on the phone's own screen
        v.deviceUnlocked()
        assertFalse(v.deviceUnlock); assertTrue(http.called("/session/factors/DEVICE")); assertTrue(v.screen is Screen.Result)
    }

    @Test fun cancellingTheLockScreenLeavesAClearMessageAndTheSessionOpen() {
        val v = vm(routes(), FakeSigner(locked = true)).also { it.signIn("alice", "pw") }
        v.startPayment("transfer", "BEN-42", 1500.0); v.confirmPayment(); v.signDevice()
        v.deviceUnlockCancelled()
        assertFalse(v.deviceUnlock); assertTrue(v.error!!.contains("wasn't confirmed")); assertTrue(v.screen is Screen.Verify)
    }

    @Test fun aResetKeyIsExplainedNotHidden() {
        val v = startedPayment(FakeSigner(invalid = true)); v.signDevice()
        assertTrue(v.error!!.contains("key was reset")); assertTrue(v.screen is Screen.Verify)
    }

    // ---- what the customer reads

    @Test fun theLearningProgressIsReadFromTheBanksAnswerAndExplained() {
        val v = vm(routes(), FakeSigner()).also { it.signIn("alice", "pw") }
        val l = v.me!!.learning!!
        assertTrue(l.learning); assertEquals(3, l.verifiedPayments); assertEquals(9, l.moreAtLeast)
        assertEquals(0.25f, learningProgress(l), 0.001f)
        assertTrue(learningText(l).contains("About 9 more payments") && learningText(l).contains("fingerprint or screen lock"))
    }

    @Test fun aBankThatDoesNotUseTheLearningPhaseShowsNothing() {
        val http = routes(); http.routes["GET /api/me"] = ok("""{"name":"A","balance":1,"enrolled":null,"protection":{"level":"Basic"},"payees":[],"learning":{"enabled":false,"phase":"READY"}}""")
        assertNull(vm(http, FakeSigner()).also { it.signIn("alice", "pw") }.me!!.learning)
    }
}
