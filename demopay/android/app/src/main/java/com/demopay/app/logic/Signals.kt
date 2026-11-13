package com.demopay.app.logic

import org.json.JSONObject

/**
 * Phone and network facts sent with a payment. Any field can be null (= not reported).
 * Real values come from this phone (see signals/PhoneSignals); in demo mode the presenter can override them.
 */
data class Signals(
    val callActive: Boolean? = null,
    val callMinutes: Double? = null,
    val callUnknownNumber: Boolean? = null,
    val remoteAccessActive: Boolean? = null,
    val deviceBoundHours: Double? = null,
    val oldDeviceActive: Boolean? = null,
    val simChangedHours: Double? = null,
    val pinResetHours: Double? = null,
    val bioSimilarity: Double? = null,
    val integrityFail: Boolean? = null,
    val overlayAccessibility: Boolean? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        callActive?.let { put("call_active", it) }
        callMinutes?.let { put("call_minutes", it) }
        callUnknownNumber?.let { put("call_unknown_number", it) }
        remoteAccessActive?.let { put("remote_access_active", it) }
        deviceBoundHours?.let { put("device_bound_hours", it) }
        oldDeviceActive?.let { put("old_device_active", it) }
        simChangedHours?.let { put("sim_changed_hours", it) }
        pinResetHours?.let { put("pin_reset_hours", it) }
        bioSimilarity?.let { put("bio_similarity", it) }
        integrityFail?.let { put("integrity_fail", it) }
        overlayAccessibility?.let { put("overlay_accessibility", it) }
    }

    /** Values set in `manual` win over this phone's real readings. */
    fun overriddenBy(manual: Signals): Signals = Signals(
        manual.callActive ?: callActive, manual.callMinutes ?: callMinutes, manual.callUnknownNumber ?: callUnknownNumber,
        manual.remoteAccessActive ?: remoteAccessActive, manual.deviceBoundHours ?: deviceBoundHours, manual.oldDeviceActive ?: oldDeviceActive,
        manual.simChangedHours ?: simChangedHours, manual.pinResetHours ?: pinResetHours, manual.bioSimilarity ?: bioSimilarity,
        manual.integrityFail ?: integrityFail, manual.overlayAccessibility ?: overlayAccessibility,
    )

    fun isEmpty(): Boolean = toJson().length() == 0
}

/** The presenter's on-screen switches (demo mode), turned into a Signals override. */
data class ManualToggles(
    val call: Boolean = false, val callMinutes: Double = 30.0, val callUnknown: Boolean = true,
    val remote: Boolean = false, val sim: Boolean = false, val simHours: Double = 2.0,
    val newPhone: Boolean = false, val bindHours: Double = 1.0, val pinReset: Boolean = false, val oddTouch: Boolean = false,
) {
    fun toSignals(): Signals = Signals(
        callActive = if (call) true else null,
        callMinutes = if (call) callMinutes else null,
        callUnknownNumber = if (call) callUnknown else null,
        remoteAccessActive = if (remote) true else null,
        simChangedHours = if (sim) simHours else null,
        deviceBoundHours = if (newPhone) bindHours else null,
        oldDeviceActive = if (newPhone) false else null,
        pinResetHours = if (pinReset) 1.0 else null,
        bioSimilarity = if (oddTouch) 0.25 else null,
    )

    companion object {
        fun from(s: Signals): ManualToggles = ManualToggles(
            call = s.callActive == true, callMinutes = s.callMinutes ?: 30.0, callUnknown = s.callUnknownNumber ?: true,
            remote = s.remoteAccessActive == true, sim = s.simChangedHours != null, simHours = s.simChangedHours ?: 2.0,
            newPhone = s.deviceBoundHours != null, bindHours = s.deviceBoundHours ?: 1.0, pinReset = s.pinResetHours != null,
            oddTouch = s.bioSimilarity != null,
        )
    }
}
