package com.demopay.app.logic

import java.time.Instant
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

/** "Rs 1,00,000" style (Indian digit grouping). */
fun rupees(n: Double): String {
    val negative = n < 0
    val cents = (abs(n) * 100).roundToInt()
    val whole = (cents / 100).toString()
    val frac = cents % 100
    val grouped = if (whole.length <= 3) whole else {
        val head = whole.substring(0, whole.length - 3)
        val pieces = head.reversed().chunked(2).joinToString(",").reversed()
        "$pieces,${whole.takeLast(3)}"
    }
    return (if (negative) "-" else "") + "Rs " + grouped + (if (frac > 0) ".%02d".format(frac) else "")
}

/** The warning's Continue button stays off until the minimum read time passed, the box is ticked and every question is answered. */
fun warningReady(secondsLeft: Int, acknowledged: Boolean, answers: Map<String, String>, questionIds: List<String>): Boolean =
    secondsLeft <= 0 && acknowledged && questionIds.all { answers[it] != null }

/** Whole seconds until an ISO-8601 instant such as "2026-11-06T12:00:00.123456Z" (never negative). */
fun secondsUntil(releaseIso: String, nowMs: Long): Int {
    val end = Instant.parse(releaseIso).toEpochMilli()
    return max(0, ceil((end - nowMs) / 1000.0).toInt())
}

fun mmss(totalSeconds: Int): String = "%02d:%02d".format(totalSeconds / 60, totalSeconds % 60)

fun percent(p: Double, digits: Int = 3): String = "%.${digits}f%%".format(p * 100)

fun readableTime(seconds: Double): String = if (seconds >= 60) "${(seconds / 60).roundToInt()} min" else "${seconds.roundToInt()}s"

/** Where the phone should find the server. The emulator reaches the computer's localhost at 10.0.2.2. */
fun normaliseBaseUrl(input: String): String {
    var s = input.trim().trimEnd('/')
    if (s.isNotEmpty() && !s.startsWith("http://") && !s.startsWith("https://")) s = "http://$s"
    return s
}

fun isValidBaseUrl(input: String): Boolean {
    val s = normaliseBaseUrl(input)
    return Regex("^https?://[A-Za-z0-9._-]+(:\\d{1,5})?$").matches(s)
}
