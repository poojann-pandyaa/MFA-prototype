package com.adaptivemfa.app.data

import android.content.Context
import java.util.UUID

/** What the app remembers between launches. An interface so the ViewModel can be tested without Android. */
interface Settings {
    var username: String?
    var baseUrl: String
    val deviceId: String
}

/** Small on-device settings. No password, token or face data is stored on the phone. */
class Store(context: Context) : Settings {
    private val p = context.getSharedPreferences("adaptive_mfa", Context.MODE_PRIVATE)

    override var username: String?
        get() = p.getString("username", null)
        set(v) = p.edit().apply { if (v == null) remove("username") else putString("username", v) }.apply()

    /** The demo bank's address. 10.0.2.2 is how the Android emulator reaches the computer's localhost; on a real phone use the computer's LAN address. */
    override var baseUrl: String
        get() = p.getString("baseUrl", DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(v) = p.edit().putString("baseUrl", v).apply()

    /**
     * One id per install. The bank trusts the phone an account was opened on; AMFA sees it as the device of every approval.
     */
    override val deviceId: String
        get() = p.getString("deviceId", null)
            ?: ("android_" + UUID.randomUUID().toString().replace("-", "").take(12)).also { p.edit().putString("deviceId", it).apply() }

    companion object {
        const val DEFAULT_BASE_URL = "http://10.0.2.2:8050"
    }
}
