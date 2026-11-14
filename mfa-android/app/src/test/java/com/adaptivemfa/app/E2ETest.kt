package com.adaptivemfa.app

import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.AmfaLink
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.E2E
import com.adaptivemfa.app.data.E2EKey
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.data.PinStore
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.KeyPair
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPrivateKeySpec
import java.security.spec.ECPublicKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** The phone's half of the end-to-end encryption; the server's half is tests/core/test_e2e.py and they share one vector. */
class E2ETest {
    private val vec = JSONObject(File("../../tests/core/e2e_vector.json").readText())
    private val params: ECParameterSpec = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }.getParameterSpec(ECParameterSpec::class.java)
    private val kf = KeyFactory.getInstance("EC")

    private fun privateOf(hex: String) = kf.generatePrivate(ECPrivateKeySpec(BigInteger(hex, 16), params))
    private fun publicOf(b64: String) = E2E.b64d(b64).let { kf.generatePublic(ECPublicKeySpec(ECPoint(BigInteger(1, it.copyOfRange(1, 33)), BigInteger(1, it.copyOfRange(33, 65))), params)) }
    private val serverKey = E2EKey(vec.getString("kid"), vec.getString("serverPublic"), E2E.sha256Hex(E2E.b64d(vec.getString("serverPublic"))))

    /** What the core does: open a box with the server's private key. */
    private fun open(box: JSONObject, aad: String): String {
        val shared = KeyAgreement.getInstance("ECDH").apply { init(privateOf(vec.getString("serverPrivate"))); doPhase(publicOf(box.getString("epk")), true) }.generateSecret()
        val nonce = E2E.b64d(box.getString("nonce"))
        val key = E2E.hkdf(shared, nonce, "amfa-e2e-v1|${box.getString("kid")}|$aad".toByteArray())
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce)); updateAAD(aad.toByteArray()) }
        return String(c.doFinal(E2E.b64d(box.getString("ct"))))
    }

    @Test fun theSameInputsGiveTheServersBox() {
        val eph = KeyPair(publicOf(vec.getString("ephemeralPublic")), privateOf(vec.getString("ephemeralPrivate")))
        val box = E2E.encrypt(serverKey, vec.getString("plaintextJson"), vec.getString("aad"), eph, E2E.b64d(vec.getString("nonce")))
        assertEquals(vec.getString("ct"), box.getString("ct"))
        assertEquals(vec.getString("ephemeralPublic"), box.getString("epk"))
    }

    @Test fun aFreshBoxOpensForItsUseOnlyAndIsNewEachTime() {
        val a = E2E.encrypt(serverKey, """{"frames":["x"]}""", "s|FACE|c")
        val b = E2E.encrypt(serverKey, """{"frames":["x"]}""", "s|FACE|c")
        assertEquals("""{"frames":["x"]}""", open(a, "s|FACE|c"))
        assertTrue(a.getString("ct") != b.getString("ct") && a.getString("epk") != b.getString("epk"))
        try { open(a, "s|FACE|other"); fail() } catch (e: Exception) { /* a box cannot be moved to another challenge */ }
    }

    // ---- through the API client

    private val link = AmfaLink("http://amfa:8040", "tok-1")
    private fun keyRoute() = "GET /c/v1/e2e-key" to ok("""{"kid":"${serverKey.kid}","alg":"x","publicKey":"${serverKey.publicKey}","fingerprint":"${serverKey.fingerprint}"}""")
    private val session = "GET /c/v1/session" to ok("""{"sessionId":"sess_1","status":"PENDING","steps":[]}""")
    private val faceOk = "POST /c/v1/session/factors/FACE" to ok("""{"result":{"status":"PASSED"},"state":{"sessionId":"sess_1","status":"APPROVED","steps":[]}}""")

    private class Pins(var value: String? = null) : PinStore {
        override fun pin(base: String) = value
        override fun setPin(base: String, fingerprint: String) { value = fingerprint }
    }

    @Test fun secureSubmissionsSendOnlyABoxBoundToTheSessionFactorAndChallenge() {
        val http = RoutedHttp(keyRoute(), session, faceOk)
        val pins = Pins()
        val api = AmfaApi(http, link, secure = true, pins = pins)
        api.session()
        api.submit("FACE", "ch_x", JSONObject().put("frames", org.json.JSONArray(listOf("data:image/jpeg;base64,PICTURE"))))
        val sent = JSONObject(http.last("/session/factors/FACE").body!!)
        assertFalse(sent.toString().contains("PICTURE"))
        val payload = sent.getJSONObject("payload")
        assertEquals(listOf("e2e"), payload.keys().asSequence().toList())
        assertTrue(open(payload.getJSONObject("e2e"), "sess_1|FACE|ch_x").contains("PICTURE"))
        assertEquals(serverKey.fingerprint, pins.value)                         // first use pins the key
    }

    @Test fun enrolmentBoxesAreBoundToTheEnrolmentLink() {
        val http = RoutedHttp(keyRoute(), "POST /c/v1/enrollment/face" to ok("""{"enrolled":true}"""))
        AmfaApi(http, link, secure = true).enrollFace(listOf("data:image/jpeg;base64,PICTURE"))
        val box = JSONObject(http.last("/enrollment/face").body!!).getJSONObject("e2e")
        assertTrue(open(box, "enroll|${E2E.sha256Hex("tok-1".toByteArray())}|FACE").contains("PICTURE"))
    }

    @Test fun oneTimeCodesStayPlain() {
        val http = RoutedHttp(session, "POST /c/v1/session/factors/OTP" to ok("""{"result":{"status":"PASSED"},"state":{"status":"APPROVED","steps":[]}}"""))
        AmfaApi(http, link, secure = true).submit("OTP", "ch_o", JSONObject().put("code", "123456"))
        assertEquals("123456", JSONObject(http.last("/session/factors/OTP").body!!).getJSONObject("payload").getString("code"))
        assertFalse(http.called("/e2e-key"))
    }

    @Test fun aChangedKeyOrNoKeyMeansNothingIsSent() {
        val changed = RoutedHttp(keyRoute(), session, faceOk)
        try { AmfaApi(changed, link, secure = true, pins = Pins("0".repeat(64))).submit("FACE", "ch_x", JSONObject()); fail() }
        catch (e: ApiException) { assertEquals("E2E_KEY_CHANGED", e.code) }
        assertFalse(changed.called("/session/factors/FACE"))

        val none = RoutedHttp(session, faceOk)                                  // key endpoint unreachable: fail closed, no plaintext fallback
        try { AmfaApi(none, link, secure = true).submit("FACE", "ch_x", JSONObject()); fail() } catch (e: ApiException) { /* readable error */ }
        assertFalse(none.called("/session/factors/FACE"))

        val lying = RoutedHttp("GET /c/v1/e2e-key" to ok("""{"kid":"k","alg":"x","publicKey":"${serverKey.publicKey}","fingerprint":"deadbeef"}"""), session, faceOk)
        try { AmfaApi(lying, link, secure = true).submit("FACE", "ch_x", JSONObject()); fail() } catch (e: ApiException) { assertEquals("E2E_KEY_INVALID", e.code) }
    }

    @Test fun anOlderServerIsExplainedAndNothingIsSent() {
        val http = RoutedHttp("GET /c/v1/e2e-key" to HttpResult(404, """{"detail":"Not Found"}"""), "POST /c/v1/enrollment/face" to ok("""{"enrolled":true}"""))
        try { AmfaApi(http, link, secure = true).enrollFace(listOf("x")); fail() } catch (e: ApiException) {
            assertEquals("E2E_UNSUPPORTED", e.code); assertTrue(e.message.contains("older than the app"))
        }
        assertFalse(http.called("/enrollment/face"))
    }

    @Test fun withoutSecureTheOldPlainBodyIsUnchanged() {
        val http = RoutedHttp(faceOk)
        AmfaApi(http, link).submit("FACE", "ch_x", JSONObject().put("frames", org.json.JSONArray(listOf("a"))))
        assertEquals(1, JSONObject(http.last("/session/factors/FACE").body!!).getJSONObject("payload").getJSONArray("frames").length())
        assertFalse(http.called("/e2e-key"))
    }
}
