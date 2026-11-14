package com.adaptivemfa.app.logic

import java.util.Base64

/** Same burst as the web client (frontend/src/components/ChallengeCameraCapture.jsx). */
object Burst {
    const val FRAME_COUNT = 13
    const val INTERVAL_MS = 150L
    /** Give up if the camera cannot deliver the frames within this many ticks. */
    const val MAX_TICKS = FRAME_COUNT * 3
}

object Challenge {
    /** Shown while frames are being captured. */
    fun prompt(type: String): String = when (type) {
        "blink" -> "Blink naturally"
        "turn_left" -> "Turn your head to your left"
        "turn_right" -> "Turn your head to your right"
        else -> "Hold still"
    }

    /**
     * Shown before capture starts. The backend compares the first frames of the burst with the last
     * ones, so the user must START facing the camera and only move once capture begins.
     */
    fun instructions(type: String): String = when (type) {
        "blink" -> "Look straight at the camera. Press the button, then blink once."
        "turn_left" -> "Look straight at the camera. Press the button, then slowly turn your head to your left and hold it until capture finishes."
        "turn_right" -> "Look straight at the camera. Press the button, then slowly turn your head to your right and hold it until capture finishes."
        else -> "Look straight at the camera and press the button."
    }
}

/** The backend accepts base64 with or without the data-URL prefix; send what the web app sends. */
fun jpegDataUrl(jpeg: ByteArray): String = "data:image/jpeg;base64," + Base64.getEncoder().encodeToString(jpeg)

/**
 * Cleans what the user typed in Settings: adds http:// if missing and removes trailing slashes.
 * Returns null when it cannot be a server address.
 */
fun normalizeBaseUrl(input: String): String? {
    val t = input.trim()
    if (t.isEmpty() || t.contains(' ')) return null
    val withScheme = if (t.startsWith("http://") || t.startsWith("https://")) t else "http://$t"
    val host = withScheme.substringAfter("://").substringBefore('/').substringBefore(':')
    return if (host.isEmpty()) null else withScheme.trimEnd('/')
}

/** The server stores naive UTC timestamps such as 2026-11-14T10:05:33.123456. */
fun formatTimestamp(raw: String): String {
    if (raw.length < 16 || raw[10] != 'T') return raw
    return raw.substring(0, 10) + " " + raw.substring(11, 16) + " UTC"
}

/** Username rules are the server's; this only stops obviously empty submissions. */
fun canSubmit(username: String, password: String): Boolean = username.isNotBlank() && password.isNotEmpty()
