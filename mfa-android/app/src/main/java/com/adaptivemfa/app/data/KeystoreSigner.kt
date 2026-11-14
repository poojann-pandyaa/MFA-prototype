package com.adaptivemfa.app.data

import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.security.keystore.UserNotAuthenticatedException
import org.json.JSONObject
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec

/**
 * The Android Keystore implementation of [DeviceSigner]. The key is made inside the secure hardware (StrongBox when the phone
 * has it), cannot be exported, and signs only for [AUTH_WINDOW_S] seconds after the phone's fingerprint or screen lock has been
 * confirmed (KeyguardManager's confirm-credential screen). ponytail: a window, not one prompt per signature; use BiometricPrompt
 * with a CryptoObject (androidx.biometric) if a bank wants each payment to need its own touch.
 */
class KeystoreSigner(private val context: Context) : DeviceSigner {
    private val alias = "amfa_device_key"
    private val store: KeyStore get() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    override fun available(): Boolean = context.getSystemService(KeyguardManager::class.java)?.isDeviceSecure == true

    override fun hasKey(): Boolean = store.containsAlias(alias)

    override fun createKey(): JSONObject {
        store.deleteEntry(alias)
        fun spec(strongBox: Boolean) = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .apply {
                if (Build.VERSION.SDK_INT >= 30) setUserAuthenticationParameters(AUTH_WINDOW_S, KeyProperties.AUTH_DEVICE_CREDENTIAL or KeyProperties.AUTH_BIOMETRIC_STRONG)
                else @Suppress("DEPRECATION") setUserAuthenticationValidityDurationSeconds(AUTH_WINDOW_S)
                if (strongBox && Build.VERSION.SDK_INT >= 28) setIsStrongBoxBacked(true)
            }.build()
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        val pair = try { gen.initialize(spec(true)); gen.generateKeyPair() }
        catch (_: StrongBoxUnavailableException) { gen.initialize(spec(false)); gen.generateKeyPair() }
        return DeviceKeys.jwk(pair.public as ECPublicKey)
    }

    override fun publicJwk(): JSONObject? = (store.getCertificate(alias)?.publicKey as? ECPublicKey)?.let { DeviceKeys.jwk(it) }

    override fun sign(message: String): String {
        val key = store.getKey(alias, null) as? PrivateKey ?: throw DeviceKeyInvalid()
        try {
            val s = Signature.getInstance("SHA256withECDSA")
            s.initSign(key)
            s.update(message.toByteArray())
            return E2E.b64e(s.sign())
        } catch (e: UserNotAuthenticatedException) {
            throw DeviceAuthRequired()
        } catch (e: KeyPermanentlyInvalidatedException) {
            throw DeviceKeyInvalid()
        }
    }

    companion object { const val AUTH_WINDOW_S = 15 }
}
