package com.adaptivemfa.app.logic

import com.adaptivemfa.app.data.Helper
import java.util.Base64
import java.util.Locale

object Challenge {
    /** Shown under the circle. The circle itself says what to do next and when it is right. */
    fun instructions(action: String?): String = when (action) {
        "TURN_LEFT" -> "Look straight at the camera until the circle turns green, then slowly turn your head to your left and hold it until it turns green again."
        "TURN_RIGHT" -> "Look straight at the camera until the circle turns green, then slowly turn your head to your right and hold it until it turns green again."
        else -> "Look straight at the camera."
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
fun registrationProblem(username: String, name: String, phone: String, email: String, password: String, confirm: String): String? =
    registrationProblems(username, name, phone, email, password, confirm).values.firstOrNull()

/** Every problem with the sign-up form, keyed by the field it belongs to ("name", "email", "phone", "username", "password"), in form order. */
fun registrationProblems(username: String, name: String, phone: String, email: String, password: String, confirm: String): Map<String, String> = buildMap {
    if (name.trim().length < 2) put("name", "Enter your full name")
    if (!Regex("^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\\.[A-Za-z]{2,24}$").matches(email.trim())) put("email", "Enter a valid email address")
    if (!Regex("^\\+?[0-9]{10,13}$").matches(phone)) put("phone", "Mobile number: 10 to 13 digits")
    if (!Regex("^[a-z0-9][a-z0-9_-]{2,23}$").matches(username)) put("username", "Username: 3 to 24 lowercase letters, digits, - or _")
    if (password.length < 8) put("password", "Password: at least 8 characters")
    else if (confirm != password) put("password", "Passwords do not match")
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

// ---------------------------------------------------------------- display helpers (same wording as the web bank)

/** Keypad entry for an amount: digits and one dot, at most two decimals and eight whole digits. */
fun pressKey(text: String, key: String): String = when {
    key == "del" -> text.dropLast(1)
    key == "." -> if (text.contains('.')) text else (text.ifEmpty { "0" } + ".")
    text.contains('.') -> if (text.substringAfter('.').length >= 2) text else text + key
    text == "0" -> key
    text.length >= 8 -> text
    else -> text + key
}

/** ₹1,234 and .50, so the paise can be drawn small like on the web home screen. */
fun rupeesParts(minor: Long): Pair<String, String> {
    val whole = String.format(Locale.US, "%,d", minor / 100)
    return whole to String.format(Locale.US, ".%02d", minor % 100)
}

fun greeting(hour: Int) = if (hour < 12) "Good morning" else if (hour < 17) "Good afternoon" else "Good evening"

/** Label and chip tone for an action's status (STATUS in the web bank's ui.js). */
fun statusLabel(status: String): Pair<String, String> = when (status) {
    "AWAITING_VERIFICATION" -> "Waiting for you" to "warn"
    "UNDER_REVIEW" -> "Being reviewed" to "warn"
    "DONE" -> "Done" to "ok"
    "DECLINED" -> "Not sent, check failed" to "bad"
    "EXPIRED" -> "Expired" to "bad"
    "CANCELLED" -> "Cancelled" to ""
    "FAILED" -> "Didn't go through" to "bad"
    else -> status.lowercase().replace('_', ' ') to ""
}

/** Same rule as the web bank: drop masks and digits, take the first word longer than an initial. */
fun shortName(display: String): String {
    val words = display.replace(Regex("[•\\d]"), " ").split(Regex("\\s+")).filter { it.isNotEmpty() }
    val first = words.firstOrNull { it.replace(".", "").length > 1 } ?: words.firstOrNull() ?: ""
    return first.removeSuffix(".")
}

/** What the result screen says: mark kind (ok, warn, bad), headline and text; [rupees] is the formatted amount. */
data class Outcome(val kind: String, val icon: String, val title: String, val text: String)

/** The limits a customer may choose for "no extra checks" (rupees a day); the bank and AMFA may allow less. */
const val NO_CHECK_MIN = 500
const val NO_CHECK_MAX = 50_000

/** Whole rupees within the allowed range, or null (also for blanks, decimals and anything else). */
fun parseNoCheck(text: String): Int? = text.trim().replace(",", "").toIntOrNull()?.takeIf { it in NO_CHECK_MIN..NO_CHECK_MAX }

/** What the customer is told once a limit change is done; [waitingHours] is set when a raise has not started yet. */
fun noCheckOutcome(rupees: String, waitingHours: Long?): Outcome =
    if (waitingHours != null) Outcome("ok", "shieldClock", "Your new limit starts later",
        "No extra checks up to $rupees a day will start in about ${maxOf(1, waitingHours)} hours. Until then your current setting stays. We wait before raising it to keep your account safe.")
    else Outcome("ok", "shieldCheck", "No extra checks up to $rupees a day",
        "Payments within this to people you've paid before, from this phone, skip the extra checks. New payees, new phones and anything unusual are still checked.")

fun deviceOutcome(): Outcome = Outcome("ok", "phone", "This phone is verified", "You can bank from it now. If you didn't do this, change your password and call us.")

/** The 8 digits a family helper's phone is given, or null. */
fun parseHelperCode(text: String): String? = text.filter { it.isDigit() }.takeIf { it.length == 8 }

/** What the customer is told once a helper invite exists. The code itself is shown on the result screen. */
fun helperOutcome(label: String): Outcome =
    Outcome("ok", "shieldCheck", "Ready for ${label.ifBlank { "your helper" }}",
        "On their phone, open this app, sign in as you, then Profile, Family helper, and enter the code below within 15 minutes. After a short wait their phone is trusted to help you pay. You can remove it any time.")

fun helperStateText(h: Helper): String = when (h.state) {
    "ACTIVE" -> "Can help you pay"
    "WAITING" -> "Starts after a short wait, for your safety"
    else -> "Waiting for them to enter the code"
}

fun outcomeFor(status: String, detail: String?, rupees: String, payee: String, bill: Boolean): Outcome = when (status) {
    "DONE" -> Outcome("ok", "check", "$rupees ${if (bill) "paid" else "sent"}", "to $payee")
    "UNDER_REVIEW" -> Outcome("warn", "shieldClock", "We're double-checking this payment",
        "$rupees to $payee is on hold while our team takes a quick look. Your money hasn't left your account.")
    "AWAITING_VERIFICATION" -> Outcome("warn", "lock", "Waiting for you", "You haven't finished confirming it's you.")
    "DECLINED" -> Outcome("bad", "ban", "We couldn't approve this",
        "We couldn't confirm it was you, so nothing was sent. If you didn't try this, call us on 1800 000 0000." + (detail?.let { " ($it)" } ?: ""))
    "EXPIRED" -> Outcome("bad", "ban", "This request timed out", "Nothing was sent. You can start again whenever you're ready.")
    "CANCELLED" -> Outcome("bad", "close", "Cancelled", "Nothing was sent or changed.")
    else -> Outcome("bad", "ban", "This didn't go through", "Nothing was sent." + (detail?.let { " ($it)" } ?: ""))
}

/** AMFA's reason codes for a rejected setup of any factor, in words. */
fun setupReason(reason: String): String = when (reason) {
    "NOT_ENOUGH_SPEECH" -> "That was too short. Please read the whole sentence clearly."
    "TOO_NOISY" -> "Find a quieter place and try again."
    "AUDIO_CLIPPED" -> "Hold the phone a little further away."
    "INCONSISTENT_SPEAKER" -> "The three recordings didn't sound like the same voice. Try again."
    "DUPLICATE_SAMPLES" -> "Those samples were identical. Please do each one for real."
    "REJECTED_SPOOF" -> "We couldn't tell a live voice was speaking. Speak to the phone directly."
    "KEYSTROKE_SAMPLES_MISSING", "NOT_ENOUGH_USABLE_SAMPLES" ->
        "We couldn't use those typing samples. Type the phrase exactly, each letter yourself, with suggestions and autocorrect turned off."
    "SHAPE_POINTER_MISMATCH", "SHAPE_POINTER_TYPES_INVALID", "INPUT_INVALID" -> "Trace the line with your finger and try again."
    else -> faceSetupReason(reason)
}
