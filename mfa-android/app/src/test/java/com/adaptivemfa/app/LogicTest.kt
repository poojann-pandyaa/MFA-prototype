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

class DisplayLogicTest {
    @Test fun amountKeypadAllowsTwoDecimalsAndEightWholeDigits() {
        fun type(vararg k: String) = k.fold("") { t, key -> com.adaptivemfa.app.logic.pressKey(t, key) }
        assertEquals("12.5", type("1", "2", ".", "5"))
        assertEquals("0.", type("."))
        assertEquals("1.25", type("1", ".", "2", "5", "9"))
        assertEquals("5", type("0", "5"))
        assertEquals("1", type("1", "2", "del"))
        assertEquals("12345678", type("1", "2", "3", "4", "5", "6", "7", "8", "9"))
        assertEquals("1.5", type("1", ".", ".", "5"))
    }

    @Test fun balanceIsSplitIntoRupeesAndPaise() {
        assertEquals("500,000" to ".00", com.adaptivemfa.app.logic.rupeesParts(50_000_000))
        assertEquals("1,234" to ".05", com.adaptivemfa.app.logic.rupeesParts(123_405))
    }

    @Test fun statusWordsMatchTheWebBank() {
        assertEquals("Done" to "ok", com.adaptivemfa.app.logic.statusLabel("DONE"))
        assertEquals("Being reviewed" to "warn", com.adaptivemfa.app.logic.statusLabel("UNDER_REVIEW"))
        assertEquals("bad", com.adaptivemfa.app.logic.statusLabel("DECLINED").second)
    }

    @Test fun outcomesReadLikeTheWebResultScreen() {
        val ok = com.adaptivemfa.app.logic.outcomeFor("DONE", null, "₹5,000.00", "V. Mehta", false)
        assertEquals("ok", ok.kind); assertEquals("₹5,000.00 sent", ok.title); assertEquals("to V. Mehta", ok.text)
        assertEquals("₹40.00 paid", com.adaptivemfa.app.logic.outcomeFor("DONE", null, "₹40.00", "Electricity", true).title)
        assertEquals("warn", com.adaptivemfa.app.logic.outcomeFor("UNDER_REVIEW", null, "₹1.00", "x", false).kind)
        val bad = com.adaptivemfa.app.logic.outcomeFor("DECLINED", "blocked", "₹1.00", "x", false)
        assertEquals("bad", bad.kind); assertTrue(bad.text.contains("(blocked)"))
        assertEquals("Mehta", com.adaptivemfa.app.logic.shortName("Mehta ••4411"))
        assertEquals("Mehta", com.adaptivemfa.app.logic.shortName("V. Mehta ••4411"))
        assertEquals("Good evening", com.adaptivemfa.app.logic.greeting(20))
    }
}

class BehaviourLogicTest {
    private fun events(a: org.json.JSONArray) = (0 until a.length()).map { a.getJSONObject(it).let { e -> Triple(e.getString("key"), e.getDouble("down"), e.getDouble("up")) } }

    @Test fun typingIsRecoveredFromTextChangesLikeTheWebSdkDoesForPhones() {
        var t = 1000.0
        val log = com.adaptivemfa.app.logic.KeystrokeLog { t }
        for ((i, text) in listOf("h", "he", "hel", "he", "hey").withIndex()) { t += 120.0 + i; log.onText(text) }
        val e = events(log.take())
        assertEquals(listOf("h", "e", "l", "Backspace", "y"), e.map { it.first })
        assertTrue(e.zipWithNext().all { (a, b) -> b.second > a.second })
        assertTrue(e.all { it.third - it.second == com.adaptivemfa.app.logic.KeystrokeLog.HOLD_MS })
    }

    @Test fun anAutocorrectedWordKeepsItsCharactersAtOneTime() {
        var t = 50.0
        val log = com.adaptivemfa.app.logic.KeystrokeLog { t }
        t = 100.0; log.onText("teh")
        t = 400.0; log.onText("the ")
        val e = events(log.take())
        // "teh" typed key by key, then "the " arrives in one change: its changed characters share one time
        assertEquals(400.0, e.last().second, 0.0)
        assertTrue(e.filter { it.second == 400.0 }.size >= 2)
    }

    @Test fun takeEmptiesTheLogAndStartsFromTheBoxContents() {
        var t = 0.0
        val log = com.adaptivemfa.app.logic.KeystrokeLog { t }
        t = 10.0; log.onText("a")
        assertEquals(1, log.take("").length())
        t = 20.0; log.onText("b")
        assertEquals(listOf("b"), events(log.take()).map { it.first })
        assertEquals(0, log.take().length())
    }

    @Test fun aSampleHoldsAtMostFourHundredEvents() {
        var t = 0.0
        val log = com.adaptivemfa.app.logic.KeystrokeLog { t }
        var text = ""
        repeat(450) { t += 5; text += "x"; log.onText(text) }
        assertEquals(400, log.take().length())
    }

    @Test fun wavHasTheStandardHeaderAndLittleEndianSamples() {
        val wav = java.util.Base64.getDecoder().decode(com.adaptivemfa.app.logic.wavBase64(shortArrayOf(1, -2, 300), 16_000))
        assertEquals("RIFF", String(wav, 0, 4)); assertEquals("WAVE", String(wav, 8, 4)); assertEquals("data", String(wav, 36, 4))
        assertEquals(44 + 6, wav.size)
        val bb = java.nio.ByteBuffer.wrap(wav).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        assertEquals(1, bb.getShort(22).toInt())          // mono
        assertEquals(16_000, bb.getInt(24))               // rate
        assertEquals(16, bb.getShort(34).toInt())         // bits
        assertEquals(6, bb.getInt(40))                    // data bytes
        assertEquals(300, bb.getShort(48).toInt())
    }

    @Test fun traceStartsNearTheGreenDotOnly() {
        val shape = listOf(0.1 to 0.5, 0.9 to 0.5)
        assertTrue(com.adaptivemfa.app.logic.nearStart(0.15, 0.52, shape))
        assertFalse(com.adaptivemfa.app.logic.nearStart(0.5, 0.5, shape))
        val j = com.adaptivemfa.app.logic.traceJson(listOf(com.adaptivemfa.app.logic.TracePoint(0.123456, 0.5, 12.3456)))
        assertEquals(0.1235, j.getJSONObject(0).getDouble("x"), 0.0); assertEquals(12.35, j.getJSONObject(0).getDouble("t"), 0.0)
    }

    @Test fun loudnessIsZeroForSilenceAndCappedAtOne() {
        assertEquals(0.0, com.adaptivemfa.app.logic.level(ShortArray(100), 100), 0.0)
        assertEquals(1.0, com.adaptivemfa.app.logic.level(ShortArray(100) { 30000 }, 100), 0.0)
        assertEquals(0.0, com.adaptivemfa.app.logic.level(ShortArray(0), 0), 0.0)
    }

    @Test fun rejectionsOfAnyFactorAreExplained() {
        assertTrue(com.adaptivemfa.app.logic.setupReason("TOO_NOISY").contains("quieter"))
        assertTrue(com.adaptivemfa.app.logic.setupReason("NOT_ENOUGH_USABLE_SAMPLES").contains("autocorrect"))
        assertTrue(com.adaptivemfa.app.logic.setupReason("LIVENESS_NOT_ESTABLISHED").contains("light"))
    }
}
