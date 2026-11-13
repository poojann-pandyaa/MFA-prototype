package com.adaptivemfa.app.data

import android.content.Context
import java.util.UUID

/** Small on-device settings. No password or face data is stored on the phone. */
class Store(context: Context) {
    private val p = context.getSharedPreferences("adaptive_mfa", Context.MODE_PRIVATE)

    var token: String?
        get() = p.getString("token", null)
        set(v) = p.edit().apply { if (v == null) remove("token") else putString("token", v) }.apply()

    var username: String?
        get() = p.getString("username", null)
        set(v) = p.edit().apply { if (v == null) remove("username") else putString("username", v) }.apply()

    /** 10.0.2.2 is how the Android emulator reaches the computer's localhost. On a real phone, use the computer's LAN address. */
    var baseUrl: String
        get() = p.getString("baseUrl", DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(v) = p.edit().putString("baseUrl", v).apply()

    /**
     * One id per install. The server only remembers it for a user after a fully successful login,
     * so after the first face check this phone counts as a known device (low risk, password only).
     */
    val deviceId: String
        get() = p.getString("deviceId", null)
            ?: ("android_" + UUID.randomUUID().toString().replace("-", "").take(12)).also { p.edit().putString("deviceId", it).apply() }

    companion object {
        const val DEFAULT_BASE_URL = "http://10.0.2.2:8000"
    }
}
