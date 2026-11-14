package com.adaptivemfa.app.logic

import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

/**
 * Typing rhythm from a soft keyboard. A phone keyboard reports no key events to the app, only the text after
 * each press, so, like the web SDK's KeystrokeRecorder for phones, every key is recovered from what a press did to
 * the text box: the letters it added and the characters it removed (Backspace). All characters of one change (an
 * autocorrected word) share its time. [now] is milliseconds from any steady clock.
 */
class KeystrokeLog(private val now: () -> Double) {
    private var text = ""
    private val events = ArrayList<Triple<String, Double, Double>>()

    /** Call with the box's new text after every change. */
    fun onText(new: String) {
        val old = text
        text = new
        var a = 0
        while (a < old.length && a < new.length && old[a] == new[a]) a++
        var b = 0
        while (b < old.length - a && b < new.length - a && old[old.length - 1 - b] == new[new.length - 1 - b]) b++
        val keys = ArrayList<String>()
        repeat(old.length - a - b) { keys += "Backspace" }
        new.substring(a, new.length - b).forEach { keys += it.toString() }
        if (keys.isEmpty()) return
        val t = Math.round(now() * 100) / 100.0
        for (k in keys) if (events.size < MAX_EVENTS) events += Triple(k, t, Math.round((t + HOLD_MS) * 100) / 100.0)
    }

    /** The events so far as the service wants them; the log starts over, and [reset] says what the box now holds. */
    fun take(textNow: String = ""): JSONArray {
        val out = JSONArray()
        events.sortedBy { it.second }.forEach { out.put(JSONObject().put("key", it.first).put("down", it.second).put("up", it.third)) }
        events.clear()
        text = textNow
        return out
    }

    companion object {
        const val MAX_EVENTS = 400
        /** Soft keyboards register a tap as a press and release about this far apart; the service ignores hold times for touch. */
        const val HOLD_MS = 3.0
    }
}

/** A traced line: points are 0..1 inside the pad, [t] in milliseconds. */
data class TracePoint(val x: Double, val y: Double, val t: Double)

fun traceJson(points: List<TracePoint>): JSONArray {
    val out = JSONArray()
    points.forEach { out.put(JSONObject().put("x", Math.round(it.x * 1e4) / 1e4).put("y", Math.round(it.y * 1e4) / 1e4).put("t", Math.round(it.t * 100) / 100.0)) }
    return out
}

/** The trace has to start near the green dot, like the web pad. */
fun nearStart(x: Double, y: Double, shape: List<Pair<Double, Double>>): Boolean {
    val s = shape.firstOrNull() ?: return true
    return Math.hypot(x - s.first, y - s.second) < 0.12
}

object Setup {
    /** Three short sentences for the voice setup (the web setup page's). */
    val SENTENCES = listOf(
        "My voice confirms my identity for my bank.",
        "Seven orange rivers flow past the quiet mountain.",
        "I approve payments only when I start them myself.",
    )
    const val MIN_CLIP_SECONDS = 2.5
    const val MAX_CLIP_SECONDS = 10
}

/** 16-bit mono PCM as a WAV file, base64 (what the web SDK's Microphone sends). */
fun wavBase64(samples: ShortArray, rate: Int = 16_000): String {
    val data = samples.size * 2
    val buf = java.nio.ByteBuffer.allocate(44 + data).order(java.nio.ByteOrder.LITTLE_ENDIAN)
    buf.put("RIFF".toByteArray()).putInt(36 + data).put("WAVE".toByteArray()).put("fmt ".toByteArray())
    buf.putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(16)
    buf.put("data".toByteArray()).putInt(data)
    samples.forEach { buf.putShort(it) }
    return Base64.getEncoder().encodeToString(buf.array())
}

/** Loudness 0..1 of a block of samples, for the meter. */
fun level(block: ShortArray, n: Int): Double {
    if (n <= 0) return 0.0
    var sum = 0.0
    for (i in 0 until n) sum += block[i].toDouble() * block[i]
    return Math.min(1.0, Math.sqrt(sum / n) / 32768.0 * 4)
}
