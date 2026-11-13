package com.demopay.app.data

import android.content.Context
import java.util.UUID

/** Small on-device settings. No payment details or personal templates are stored here. */
class Store(context: Context) {
    private val p = context.getSharedPreferences("demopay", Context.MODE_PRIVATE)

    var token: String?
        get() = p.getString("token", null)
        set(v) = p.edit().apply { if (v == null) remove("token") else putString("token", v) }.apply()

    /** 10.0.2.2 is how the Android emulator reaches the computer's localhost. On a real phone, use the computer's LAN address. */
    var baseUrl: String
        get() = p.getString("baseUrl", DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(v) = p.edit().putString("baseUrl", v).apply()

    var useRealSignals: Boolean
        get() = p.getBoolean("useRealSignals", true)
        set(v) = p.edit().putBoolean("useRealSignals", v).apply()

    val deviceId: String
        get() = p.getString("deviceId", null) ?: ("and-" + UUID.randomUUID().toString().take(8)).also { p.edit().putString("deviceId", it).apply() }

    companion object {
        const val DEFAULT_BASE_URL = "http://10.0.2.2:8070"
    }
}
