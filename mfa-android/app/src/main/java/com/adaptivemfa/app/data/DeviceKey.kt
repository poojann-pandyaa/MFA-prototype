package com.adaptivemfa.app.data

import org.json.JSONObject
import java.math.BigInteger
import java.security.interfaces.ECPublicKey

/** The phone's own lock screen has to be confirmed again before the key will sign. */
class DeviceAuthRequired : Exception("Confirm with your fingerprint or screen lock")

/** The key was reset by the phone (a new lock screen, for example) and cannot sign any more. */
class DeviceKeyInvalid : Exception("This phone's key was reset")

/**
 * A key in the phone's secure hardware that opens only to the phone's fingerprint or screen lock. AMFA sends a challenge for each
 * payment and the key signs it: that proves this phone (possession) and the person who unlocked it (fingerprint or PIN).
 * An interface so the app can be tested without a phone.
 */
interface DeviceSigner {
    /** False when the phone has no screen lock: a key anyone could use proves nothing, so none is made. */
    fun available(): Boolean
    fun hasKey(): Boolean
    /** Makes the key (replacing any earlier one) and returns its public half as a JWK. */
    fun createKey(): JSONObject
    fun publicJwk(): JSONObject?
    /** Signs [message] (ES256, DER). Throws [DeviceAuthRequired] until the lock screen has been confirmed, [DeviceKeyInvalid] if the key is gone. */
    fun sign(message: String): String
}

object NoDeviceSigner : DeviceSigner {
    override fun available() = false
    override fun hasKey() = false
    override fun createKey(): JSONObject = throw DeviceKeyInvalid()
    override fun publicJwk(): JSONObject? = null
    override fun sign(message: String): String = throw DeviceKeyInvalid()
}

object DeviceKeys {
    /** The public key as AMFA reads it: EC P-256, x and y as 32-byte base64url. */
    fun jwk(pub: ECPublicKey): JSONObject {
        fun fixed(v: BigInteger): ByteArray = v.toByteArray().let { if (it.size > 32) it.copyOfRange(it.size - 32, it.size) else ByteArray(32 - it.size) + it }
        return JSONObject().put("kty", "EC").put("crv", "P-256")
            .put("x", E2E.b64e(fixed(pub.w.affineX))).put("y", E2E.b64e(fixed(pub.w.affineY)))
    }
}
