package com.adaptivemfa.app

import com.adaptivemfa.app.logic.Burst
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.canSubmit
import com.adaptivemfa.app.logic.formatTimestamp
import com.adaptivemfa.app.logic.jpegDataUrl
import com.adaptivemfa.app.logic.normalizeBaseUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class LogicTest {
    @Test fun burstMatchesTheWebClientAndServerLimits() {
        assertEquals(13, Burst.FRAME_COUNT)
        assertEquals(150L, Burst.INTERVAL_MS)
        // backend/config.py: LIVENESS_MAX_FRAMES = 30, LIVENESS_MIN_VALID_FRAMES = 6
        assertTrue(Burst.FRAME_COUNT in 6..30)
    }

    @Test fun everyServerChallengeHasItsOwnPrompt() {
        for (type in listOf("blink", "turn_left", "turn_right")) {
            assertFalse(Challenge.prompt(type) == Challenge.prompt("unknown"))
            assertTrue(Challenge.instructions(type).startsWith("Look straight at the camera"))
        }
        assertTrue(Challenge.prompt("turn_left").contains("left"))
        assertTrue(Challenge.prompt("turn_right").contains("right"))
    }

    @Test fun dataUrlRoundTrips() {
        val bytes = byteArrayOf(-1, -40, -1, 0, 1, 2, 3)   // JPEG magic + payload
        val url = jpegDataUrl(bytes)
        assertTrue(url.startsWith("data:image/jpeg;base64,"))
        // The server splits on the comma, then base64-decodes (backend/services/biometrics.py).
        assertTrue(Base64.getDecoder().decode(url.split(",")[1]).contentEquals(bytes))
    }

    @Test fun serverAddressIsCleanedUp() {
        assertEquals("http://192.168.1.20:8000", normalizeBaseUrl(" 192.168.1.20:8000/ "))
        assertEquals("https://mfa.example.com", normalizeBaseUrl("https://mfa.example.com//"))
        assertEquals("http://10.0.2.2:8000", normalizeBaseUrl("http://10.0.2.2:8000"))
        assertNull(normalizeBaseUrl(""))
        assertNull(normalizeBaseUrl("http://"))
        assertNull(normalizeBaseUrl("not an address"))
    }

    @Test fun timestampsAreShortenedAndMarkedUtc() {
        assertEquals("2026-11-14 10:05 UTC", formatTimestamp("2026-11-14T10:05:33.123456"))
        assertEquals("weird", formatTimestamp("weird"))
    }

    @Test fun submitNeedsUsernameAndPassword() {
        assertTrue(canSubmit("alice", "pw"))
        assertFalse(canSubmit("  ", "pw"))
        assertFalse(canSubmit("alice", ""))
    }
}
