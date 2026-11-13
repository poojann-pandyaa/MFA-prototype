package com.demopay.app.signals

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.os.Build
import android.provider.Settings
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import com.demopay.app.logic.Signals
import java.io.File

/**
 * Real facts read from THIS phone, as yes/no flags. Nothing identifying leaves the phone:
 *  - call in progress and how long (never the number, contacts or audio)
 *  - an extra display is connected (screen mirroring or casting is a common remote-help setup)
 *  - an accessibility service is enabled (screen readers are normal; some malware abuses them)
 *  - the phone looks rooted (root helper binaries or test-signed build; emulators are not counted)
 *
 * Only positives are reported, and anything the phone cannot know (SIM swap, new device binding, typing rhythm) is left out.
 * A real app would also use Play Integrity for device integrity; that needs a Play-enabled device and a cloud project.
 */
class PhoneSignals(private val context: Context) {
    private val tm: TelephonyManager? = context.getSystemService(TelephonyManager::class.java)
    private var started = false
    private var callActive = false
    private var callStartMs = 0L
    private var legacyListener: PhoneStateListener? = null
    private var callback: Any? = null

    fun hasPhonePermission(): Boolean = context.checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

    /** Safe to call again after the permission is granted. */
    fun start() {
        if (started || tm == null || !hasPhonePermission()) return
        started = true
        if (Build.VERSION.SDK_INT >= 31) {
            val cb = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                override fun onCallStateChanged(state: Int) = handle(state)
            }
            callback = cb
            tm.registerTelephonyCallback(context.mainExecutor, cb)
        } else {
            @Suppress("DEPRECATION")
            val l = object : PhoneStateListener() {
                @Deprecated("Deprecated in Java")
                override fun onCallStateChanged(state: Int, phoneNumber: String?) = handle(state)
            }
            legacyListener = l
            @Suppress("DEPRECATION")
            tm.listen(l, PhoneStateListener.LISTEN_CALL_STATE)
        }
    }

    fun stop() {
        if (!started || tm == null) return
        started = false
        if (Build.VERSION.SDK_INT >= 31) {
            (callback as? TelephonyCallback)?.let { tm.unregisterTelephonyCallback(it) }
        } else {
            @Suppress("DEPRECATION")
            legacyListener?.let { tm.listen(it, PhoneStateListener.LISTEN_NONE) }
        }
        callback = null
        legacyListener = null
    }

    private fun handle(state: Int) {
        when (state) {
            TelephonyManager.CALL_STATE_OFFHOOK -> if (!callActive) { callActive = true; callStartMs = System.currentTimeMillis() }
            TelephonyManager.CALL_STATE_IDLE -> callActive = false
        }
    }

    private fun callMinutes(): Double = if (callActive) (System.currentTimeMillis() - callStartMs) / 60_000.0 else 0.0

    private fun extraDisplay(): Boolean = try {
        (context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).displays.size > 1
    } catch (e: Exception) { false }

    private fun accessibilityOn(): Boolean = try {
        !Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).isNullOrBlank()
    } catch (e: Exception) { false }

    private fun isEmulator(): Boolean = Build.FINGERPRINT.startsWith("generic") || Build.FINGERPRINT.contains("emulator") ||
        Build.HARDWARE.contains("ranchu") || Build.HARDWARE.contains("goldfish") || Build.MODEL.contains("Emulator") || Build.MODEL.contains("sdk_gphone")

    private fun looksRooted(): Boolean {
        if (isEmulator()) return false
        val paths = listOf("/system/app/Superuser.apk", "/sbin/su", "/system/bin/su", "/system/xbin/su", "/data/local/xbin/su", "/data/local/bin/su", "/data/local/su")
        return paths.any { File(it).exists() } || (Build.TAGS?.contains("test-keys") == true)
    }

    /** What would be sent with a payment right now (positives only). */
    fun snapshot(): Signals {
        val inCall = started && callActive
        return Signals(
            callActive = if (inCall) true else null,
            callMinutes = if (inCall) callMinutes() else null,
            remoteAccessActive = if (extraDisplay()) true else null,
            integrityFail = if (looksRooted()) true else null,
            overlayAccessibility = if (accessibilityOn()) true else null,
        )
    }

    /** Human-readable list for the Settings screen: what this phone can and cannot tell the app. */
    fun report(): List<Pair<String, String>> = listOf(
        "Phone call in progress" to when {
            !hasPhonePermission() -> "not available (permission not granted)"
            callActive -> "yes, for %.1f min".format(callMinutes())
            else -> "no"
        },
        "Screen mirroring / extra display" to if (extraDisplay()) "yes" else "no",
        "Accessibility service enabled" to if (accessibilityOn()) "yes" else "no",
        "Phone looks rooted" to if (isEmulator()) "not checked (emulator)" else if (looksRooted()) "yes" else "no",
        "SIM change, new-device binding, typing rhythm" to "not available on this phone (needs the telecom or a trusted SDK)",
    )
}
