package com.demopay.app

import com.demopay.app.logic.SCENARIOS
import com.demopay.app.logic.isValidBaseUrl
import com.demopay.app.logic.mmss
import com.demopay.app.logic.normaliseBaseUrl
import com.demopay.app.logic.percent
import com.demopay.app.logic.readableTime
import com.demopay.app.logic.rupees
import com.demopay.app.logic.secondsUntil
import com.demopay.app.logic.warningReady
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class LogicTest {
    @Test fun rupeesUsesIndianGrouping() {
        assertEquals("Rs 300", rupees(300.0))
        assertEquals("Rs 1,000", rupees(1000.0))
        assertEquals("Rs 1,00,000", rupees(100000.0))
        assertEquals("Rs 2,40,000", rupees(240000.0))
        assertEquals("Rs 1,23,45,678", rupees(12345678.0))
        assertEquals("Rs 1,234.50", rupees(1234.5))
        assertEquals("Rs 0", rupees(0.0))
        assertEquals("-Rs 500", rupees(-500.0))
    }

    @Test fun warningNeedsReadTimeTickAndEveryAnswer() {
        val ids = listOf("a", "b")
        assertFalse(warningReady(3, true, mapOf("a" to "no", "b" to "no"), ids))        // still reading
        assertFalse(warningReady(0, false, mapOf("a" to "no", "b" to "no"), ids))       // box not ticked
        assertFalse(warningReady(0, true, mapOf("a" to "no"), ids))                      // a question unanswered
        assertTrue(warningReady(0, true, mapOf("a" to "no", "b" to "yes"), ids))
    }

    @Test fun holdCountdownNeverGoesNegative() {
        val now = Instant.parse("2026-11-06T12:00:00Z").toEpochMilli()
        assertEquals(20, secondsUntil("2026-11-06T12:00:20Z", now))
        assertEquals(21, secondsUntil("2026-11-06T12:00:20.400000Z", now))                // server sends microseconds
        assertEquals(0, secondsUntil("2026-11-06T11:59:00Z", now))
        assertEquals("00:20", mmss(20)); assertEquals("30:00", mmss(1800)); assertEquals("00:00", mmss(0))
    }

    @Test fun formatting() {
        assertEquals("1.061%", percent(0.01061))
        assertEquals("6s", readableTime(6.0)); assertEquals("30 min", readableTime(1800.0))
    }

    @Test fun serverAddressIsCleanedAndChecked() {
        assertEquals("http://10.0.2.2:8070", normaliseBaseUrl(" 10.0.2.2:8070/ "))
        assertEquals("https://example.com", normaliseBaseUrl("https://example.com/"))
        assertTrue(isValidBaseUrl("http://192.168.1.20:8070")); assertTrue(isValidBaseUrl("10.0.2.2:8070"))
        assertFalse(isValidBaseUrl("")); assertFalse(isValidBaseUrl("http://bad host:8070")); assertFalse(isValidBaseUrl("http://x:99999999"))
    }

    @Test fun scenariosAreWellFormed() {
        assertEquals(SCENARIOS.size, SCENARIOS.map { it.name }.toSet().size)
        assertTrue(SCENARIOS.all { it.amount > 0 && it.payee.contains("@") && it.channel in listOf("pay", "collect", "qr") && it.hint.isNotBlank() })
        val personas = setOf("asha", "ramesh", "kabir", "meera")
        assertTrue(SCENARIOS.all { it.persona in personas })
    }
}
