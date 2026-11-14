package com.adaptivemfa.app.data

import org.json.JSONObject
import java.math.BigInteger
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** What the core publishes at /c/v1/e2e-key. */
data class E2EKey(val kid: String, val publicKey: String, val fingerprint: String)

/**
 * End-to-end encryption to AMFA's core (see amfa/common/e2e.py, the other half of this): ECIES with P-256 ECDH, HKDF-SHA256 and
 * AES-256-GCM, from the platform's own crypto. What the phone sends (pictures, recordings, typing) can then be read only by the
 * core, not by a tunnel, a proxy or the bank. [aad] ties a box to one use, so a captured box cannot be replayed elsewhere.
 */
object E2E {
    // DER header of a P-256 SubjectPublicKeyInfo; the 65-byte uncompressed point follows
    private val SPKI_P256 = hex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
    private val random = SecureRandom()

    fun b64e(b: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(b)
    fun b64d(s: String): ByteArray = Base64.getUrlDecoder().decode(s)
    fun sha256Hex(b: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }

    /** Is the published fingerprint really that of the published key? (A cheap check; pinning is what resists a proxy.) */
    fun consistent(k: E2EKey): Boolean = runCatching { sha256Hex(b64d(k.publicKey)) == k.fingerprint }.getOrDefault(false)

    /** [ephemeral] and [nonce] are only given by tests, to reproduce the known-answer vector shared with the server's tests. */
    fun encrypt(key: E2EKey, plaintext: String, aad: String, ephemeral: KeyPair? = null, nonce: ByteArray? = null): JSONObject {
        val server = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(SPKI_P256 + b64d(key.publicKey)))
        val eph = ephemeral ?: KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1"), random) }.generateKeyPair()
        val n = nonce ?: ByteArray(12).also { random.nextBytes(it) }
        val shared = KeyAgreement.getInstance("ECDH").apply { init(eph.private); doPhase(server, true) }.generateSecret()
        val aes = hkdf(shared, n, "amfa-e2e-v1|${key.kid}|$aad".toByteArray())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(aes, "AES"), GCMParameterSpec(128, n))
            updateAAD(aad.toByteArray())
        }
        val ct = cipher.doFinal(plaintext.toByteArray())
        return JSONObject().put("kid", key.kid).put("epk", b64e(point(eph.public as ECPublicKey))).put("nonce", b64e(n)).put("ct", b64e(ct))
    }

    /** HKDF-SHA256 for one 32-byte block (extract with [salt], expand once). */
    internal fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray): ByteArray {
        fun hmac(k: ByteArray, d: ByteArray) = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(k, "HmacSHA256")) }.doFinal(d)
        return hmac(hmac(salt, ikm), info + byteArrayOf(1))
    }

    /** 0x04 || X || Y, each 32 bytes: the form the server reads. */
    internal fun point(k: ECPublicKey): ByteArray {
        fun fixed(v: BigInteger): ByteArray = v.toByteArray().let { if (it.size > 32) it.copyOfRange(it.size - 32, it.size) else ByteArray(32 - it.size) + it }
        return byteArrayOf(4) + fixed(k.w.affineX) + fixed(k.w.affineY)
    }

    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
}

/** Remembers the fingerprint of each server's key, so a proxy that later swaps the key is noticed (trust on first use). */
interface PinStore {
    fun pin(base: String): String?
    fun setPin(base: String, fingerprint: String)
}
