package com.demopay.app

import com.demopay.app.logic.ManualToggles
import com.demopay.app.logic.Signals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SignalsTest {
    @Test fun onlyKnownValuesAreSent() {
        val j = Signals(callActive = true, callMinutes = 12.5).toJson()
        assertEquals(2, j.length())
        assertTrue(j.getBoolean("call_active")); assertEquals(12.5, j.getDouble("call_minutes"), 0.0)
        assertFalse(j.has("remote_access_active"))
        assertTrue(Signals().isEmpty())
    }

    @Test fun presenterSwitchesOverrideThePhoneReadings() {
        val real = Signals(callActive = true, callMinutes = 3.0, integrityFail = true)
        val manual = Signals(callActive = true, callMinutes = 25.0, remoteAccessActive = true)
        val merged = real.overriddenBy(manual)
        assertEquals(25.0, merged.callMinutes!!, 0.0)          // manual wins
        assertEquals(true, merged.integrityFail)               // phone reading kept where nothing was set
        assertEquals(true, merged.remoteAccessActive)
        assertNull(merged.simChangedHours)
    }

    @Test fun togglesTurnIntoTheSameSignalsTheWebAppSends() {
        val s = ManualToggles(call = true, callMinutes = 25.0, callUnknown = true, sim = true, simHours = 2.0, newPhone = true, bindHours = 1.0, pinReset = true, oddTouch = true).toSignals()
        assertEquals(true, s.callActive); assertEquals(25.0, s.callMinutes!!, 0.0); assertEquals(true, s.callUnknownNumber)
        assertEquals(2.0, s.simChangedHours!!, 0.0); assertEquals(1.0, s.deviceBoundHours!!, 0.0); assertEquals(false, s.oldDeviceActive)
        assertEquals(1.0, s.pinResetHours!!, 0.0); assertEquals(0.25, s.bioSimilarity!!, 0.0)
        assertTrue(ManualToggles().toSignals().isEmpty())
    }

    @Test fun togglesRoundTripFromAScenario() {
        val sig = Signals(callActive = true, callMinutes = 25.0, callUnknownNumber = true, simChangedHours = 2.0)
        val t = ManualToggles.from(sig)
        assertTrue(t.call && t.sim && !t.remote && !t.newPhone)
        assertEquals(25.0, t.callMinutes, 0.0)
    }
}
