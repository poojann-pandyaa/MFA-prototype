package com.adaptivemfa.app

import com.adaptivemfa.app.logic.Burst
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.FaceSetup
import com.adaptivemfa.app.logic.faceSetupReason
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.jpegDataUrl
import com.adaptivemfa.app.logic.normalizeBaseUrl
import com.adaptivemfa.app.logic.parseRupees
import com.adaptivemfa.app.logic.reachableAmfaBase
import com.adaptivemfa.app.logic.registrationProblem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class LogicTest {
    @Test fun defaultBurstFitsTheFaceServiceLimits() {
        // amfa/factors/face/service.py: VERIFY_FRAMES = (6, 24)
        assertTrue(Burst.FRAME_COUNT in 6..24)
        assertEquals(150L, Burst.INTERVAL_MS)
        assertEquals(60, Burst.maxTicks(20))
    }

    @Test fun faceSetupSendsBetweenThreeAndEightPictures() {
        // amfa/core/api_client.py FaceEnroll: 3 to 8 frames; the script starts and ends facing the camera
        assertTrue(FaceSetup.CUES.size in 3..8)
        assertEquals(FaceSetup.CUES.first(), FaceSetup.CUES.last())
        assertTrue(FaceSetup.CUES.any { it.contains("left") } && FaceSetup.CUES.any { it.contains("right") })
    }

    @Test fun everyFaceChallengeActionHasItsOwnPrompt() {
        for (a in listOf("TURN_LEFT", "TURN_RIGHT")) {
            assertFalse(Challenge.prompt(a) == Challenge.prompt(null))
            assertTrue(Challenge.instructions(a).startsWith("Look straight at the camera"))
        }
        assertTrue(Challenge.prompt("TURN_LEFT").contains("left"))
        assertTrue(Challenge.prompt("TURN_RIGHT").contains("right"))
    }

    @Test fun dataUrlIsWhatTheWebPagesSend() {
        val bytes = byteArrayOf(1, 2, 3, 4, 5)
        val url = jpegDataUrl(bytes)
        assertTrue(url.startsWith("data:image/jpeg;base64,"))
        assertEquals(bytes.toList(), Base64.getDecoder().decode(url.substringAfter(",")).toList())
    }

    @Test fun serverAddressIsCleanedUp() {
        assertEquals("http://10.0.2.2:8050", normalizeBaseUrl("  10.0.2.2:8050/ "))
        assertEquals("https://bank.example.com", normalizeBaseUrl("https://bank.example.com//"))
        assertNull(normalizeBaseUrl(""))
        assertNull(normalizeBaseUrl("not an address"))
        assertNull(normalizeBaseUrl("http://"))
    }

    @Test fun localAmfaAddressesAreRedirectedToTheBankHost() {
        assertEquals("http://10.0.2.2:8040", reachableAmfaBase("http://127.0.0.1:8040", "http://10.0.2.2:8050"))
        assertEquals("http://192.168.1.20:8040", reachableAmfaBase("http://localhost:8040", "http://192.168.1.20:8050"))
        assertEquals("https://amfa.example.com", reachableAmfaBase("https://amfa.example.com", "http://10.0.2.2:8050"))
    }

    @Test fun moneyIsShownInRupeesAndParsedFromWhatTheUserTypes() {
        assertEquals("₹500,000.00", formatMoney(50_000_000))
        assertEquals("₹0.50", formatMoney(50))
        assertEquals(1250.5, parseRupees(" 1,250.5 ")!!, 0.0)
        assertEquals(10.0, parseRupees("10")!!, 0.0)
        assertNull(parseRupees("0"))
        assertNull(parseRupees("12.345"))
        assertNull(parseRupees("abc"))
        assertNull(parseRupees(""))
    }

    @Test fun registrationFollowsTheBanksOwnRules() {
        fun p(u: String = "alice", n: String = "Alice Rao", ph: String = "9812345678", e: String = "a@example.com", pw: String = "longenough1", c: String = pw) =
            registrationProblem(u, n, ph, e, pw, c)
        assertNull(p())
        assertTrue(p(u = "Al")!!.startsWith("Username"))
        assertTrue(p(u = "UPPER")!!.startsWith("Username"))
        assertTrue(p(n = "A")!!.contains("name"))
        assertTrue(p(ph = "12345")!!.startsWith("Mobile"))
        assertTrue(p(e = "nope")!!.contains("email"))
        assertTrue(p(pw = "short")!!.startsWith("Password"))
        assertEquals("Passwords do not match", p(c = "different1"))
    }

    @Test fun rejectedFaceSetupIsExplainedInWords() {
        assertTrue(faceSetupReason("LIVENESS_NOT_ESTABLISHED").contains("light"))
        assertTrue(faceSetupReason("SOMETHING_NEW").contains("SOMETHING_NEW"))
    }
}
