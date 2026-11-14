package com.adaptivemfa.app.logic

import java.util.Base64
import java.util.Locale

/** Pacing of a face check. The frame count and interval come from AMFA's challenge; these are its defaults. */
object Burst {
    const val FRAME_COUNT = 20
    const val INTERVAL_MS = 150L

    /** Give up if the camera cannot deliver the frames within this many ticks. */
    fun maxTicks(frames: Int) = frames * 3
}

/** The face-enrolment script: five pictures, a cue before each, extra time to move after a new cue (the web setup page does the same). */
object FaceSetup {
    val CUES = listOf(
        "Look straight at the camera",
        "Look straight at the camera",
        "Turn your head slightly to your left",
        "Turn your head slightly to your right",
        "Look straight at the camera",
    )
    const val PICTURE_GAP_MS = 450L
    const val NEW_CUE_PAUSE_MS = 1_500L
}

object Challenge {
    fun prompt(action: String?): String = when (action) {
        "TURN_LEFT" -> "Turn your head to your left"
        "TURN_RIGHT" -> "Turn your head to your right"
        else -> "Hold still"
    }

    /** The server compares the first frames with the last ones, so the user must START facing the camera and move only once capture begins. */
    fun instructions(action: String?): String = when (action) {
        "TURN_LEFT" -> "Look straight at the camera. Press the button, then slowly turn your head to your left and hold it until capture finishes."
        "TURN_RIGHT" -> "Look straight at the camera. Press the button, then slowly turn your head to your right and hold it until capture finishes."
        else -> "Look straight at the camera and press the button."
    }
}

/** AMFA accepts base64 with or without the data-URL prefix; send what the web pages send. */
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

/**
 * The bank hands back AMFA links built from AMFA's own public address, which on a developer machine is
 * 127.0.0.1 or localhost: unreachable from a phone. Point those at the machine the bank was reached on.
 */
fun reachableAmfaBase(amfaBase: String, bankBase: String): String {
    val host = amfaBase.substringAfter("://").substringBefore(':').substringBefore('/')
    if (host != "127.0.0.1" && host != "localhost") return amfaBase
    val bankHost = bankBase.substringAfter("://").substringBefore('/').substringBefore(':')
    return amfaBase.replaceFirst(host, bankHost)
}

fun formatMoney(minor: Long): String = "₹" + String.format(Locale.US, "%,.2f", minor / 100.0)

/** Rupees as typed ("1,250.5") -> a number with at most two decimals, or null. */
fun parseRupees(text: String): Double? {
    val t = text.trim().replace(",", "")
    if (!Regex("""\d{1,8}(\.\d{1,2})?""").matches(t)) return null
    return t.toDouble().takeIf { it > 0 }
}

/** The bank's own sign-up rules, so the button stays off until the form can pass. Null means fine. */
fun registrationProblem(username: String, name: String, phone: String, email: String, password: String, confirm: String): String? = when {
    !Regex("^[a-z0-9][a-z0-9_-]{2,23}$").matches(username) -> "Username: 3 to 24 lowercase letters, digits, - or _"
    name.trim().length < 2 -> "Enter your full name"
    !Regex("^\\+?[0-9]{10,13}$").matches(phone) -> "Mobile number: 10 to 13 digits"
    !Regex("^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\\.[A-Za-z]{2,24}$").matches(email.trim()) -> "Enter a valid email address"
    password.length < 8 -> "Password: at least 8 characters"
    confirm != password -> "Passwords do not match"
    else -> null
}

/** AMFA's reason codes for a rejected face enrolment, in words. */
fun faceSetupReason(reason: String): String = when (reason) {
    "LIVENESS_NOT_ESTABLISHED" -> "We couldn't tell a live face is in front of the camera. Use good, even light and try again."
    "INCONSISTENT_IDENTITY" -> "The pictures didn't look like the same person. Try again, facing the camera."
    "NO_FACE_DETECTED" -> "We couldn't see your face. Face the camera in good light."
    "MULTIPLE_FACES" -> "More than one face is in view. Make sure you are alone in the picture."
    "FACE_TOO_SMALL" -> "Move closer to the camera."
    else -> "The face setup didn't work ($reason). Please try again."
}
