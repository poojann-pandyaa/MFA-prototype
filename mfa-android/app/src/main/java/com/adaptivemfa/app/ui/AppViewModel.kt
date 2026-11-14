package com.adaptivemfa.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.adaptivemfa.app.data.ActivityItem
import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.AmfaLink
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.Behaviour
import com.adaptivemfa.app.data.TotpSetup
import com.adaptivemfa.app.data.Me
import com.adaptivemfa.app.data.SessionState
import com.adaptivemfa.app.data.Started
import com.adaptivemfa.app.data.Settings
import com.adaptivemfa.app.data.Submitted
import com.adaptivemfa.app.logic.Outcome
import com.adaptivemfa.app.logic.setupReason
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.noCheckOutcome
import com.adaptivemfa.app.logic.normalizeBaseUrl
import com.adaptivemfa.app.logic.outcomeFor
import com.adaptivemfa.app.logic.reachableAmfaBase
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import com.adaptivemfa.app.logic.Setup
import com.adaptivemfa.app.logic.TracePoint
import com.adaptivemfa.app.logic.traceJson
import org.json.JSONObject

sealed interface Screen {
    data object Welcome : Screen
    data object SignIn : Screen
    data object SignUp : Screen
    /** "Let's protect your account": the step between opening an account and the face setup. */
    data object Setup : Screen
    /** AMFA's face enrolment: [consentRequired] says whether the customer still has to agree to the notice first. */
    data class FaceSetup(val consentRequired: Boolean, val consentVersion: String) : Screen
    /** Voice setup: three sentences read aloud. */
    data class VoiceSetup(val consentRequired: Boolean, val consentVersion: String) : Screen
    /** Typing setup: the phrase [Behaviour.repetitions] times and, on a phone, the traced line. */
    data class TypingSetup(val consentRequired: Boolean, val consentVersion: String, val behaviour: Behaviour) : Screen
    /** Authenticator-app setup: add [secret] to an app (or open [otpauthUri]) and confirm with the code it shows. */
    data class AuthenticatorSetup(val otpauthUri: String, val secret: String) : Screen
    data object Home : Screen
    data class Pay(val bills: Boolean = false) : Screen
    data class Amount(val kind: String, val ref: String, val display: String, val initials: String) : Screen
    /** What the customer is about to approve and which checks are coming; [Started.action] holds the plan. */
    data class Review(val started: Started) : Screen
    /** One AMFA approval in progress; [session] carries the current challenge (face, code or something unsupported). */
    data class Verify(val actionId: String, val session: SessionState) : Screen
    data class Result(val outcome: Outcome, val action: ActivityItem?) : Screen
    data object Activity : Screen
    data object Profile : Screen
    /** The customer's own "no extra checks up to this much a day". */
    data object NoCheck : Screen
    data object Settings : Screen
}

class AppViewModel(
    val store: Settings,
    private val bank: BankApi,
    private val amfaFor: (AmfaLink) -> AmfaApi,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    var screen by mutableStateOf<Screen>(Screen.Welcome)
        private set
    private var beforeSettings: Screen = Screen.Welcome

    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    var notice by mutableStateOf<String?>(null)

    var username by mutableStateOf(store.username ?: "")
        private set
    var me by mutableStateOf<Me?>(null)
        private set
    var activity by mutableStateOf<List<ActivityItem>>(emptyList())
        private set

    /** The code a demo server showed for the payment in progress (null until asked for). */
    var demoCode by mutableStateOf<String?>(null)
        private set
    private var amfa: AmfaApi? = null
    private var pending: Started? = null

    fun go(to: Screen) {
        error = null
        notice = null
        demoCode = null
        if (to == Screen.Settings && screen != Screen.Settings) beforeSettings = screen
        screen = to
    }

    /** The four bottom tabs. */
    fun tab(key: String) {
        go(when (key) { "pay" -> Screen.Pay(); "activity" -> Screen.Activity; "profile" -> Screen.Profile; else -> Screen.Home })
        if (key == "activity" || key == "home") refreshHome()
    }

    fun back() {
        when (val s = screen) {
            Screen.Settings -> go(beforeSettings)
            Screen.SignIn, Screen.SignUp -> go(Screen.Welcome)
            is Screen.FaceSetup, is Screen.VoiceSetup, is Screen.TypingSetup, is Screen.AuthenticatorSetup -> { go(Screen.Home); notice = "You can set this up later from Profile." }
            is Screen.Amount -> go(Screen.Pay(s.kind == "bill"))
            Screen.NoCheck -> go(Screen.Profile)
            is Screen.Review -> cancelReview()
            is Screen.Verify -> cancelVerify()
            is Screen.Pay -> go(Screen.Home)
            else -> Unit
        }
    }

    private fun run(block: suspend () -> Unit) {
        if (busy) return
        viewModelScope.launch {
            busy = true
            error = null
            try {
                block()
            } catch (e: ApiException) {
                if (e.status == 401 && me != null) signOut("Your session expired. Please sign in again.") else error = e.message
            } catch (e: Exception) {
                error = e.message ?: "Something went wrong."
            } finally {
                busy = false
            }
        }
    }

    private fun amfaApi(link: AmfaLink): AmfaApi =
        amfaFor(link.copy(base = reachableAmfaBase(link.base, store.baseUrl))).also { amfa = it }

    private suspend fun refresh() {
        me = withContext(io) { bank.me() }
        activity = withContext(io) { bank.activity() }
    }

    private suspend fun enter(user: String) {
        store.username = user
        username = user
        refresh()
        go(Screen.Home)
    }

    // ------------------------------------------------------------ account

    fun signIn(user: String, password: String) = run {
        val u = user.trim().lowercase()
        withContext(io) { bank.login(u, password) }
        enter(u)
    }

    fun signUp(user: String, name: String, phone: String, email: String, password: String) = run {
        val u = user.trim().lowercase()
        withContext(io) { bank.register(u, name.trim(), phone.trim(), email.trim(), password) }
        store.username = u
        username = u
        refresh()
        go(Screen.Setup)
    }

    fun logout() = run { withContext(io) { try { bank.logout() } catch (_: ApiException) { } }; signOut(null) }

    private fun signOut(message: String?) {
        me = null
        activity = emptyList()
        amfa = null
        pending = null
        go(Screen.SignIn)
        error = message
    }

    // ------------------------------------------------------------ face setup (AMFA enrolment)

    fun startFaceSetup() = startSetup("FACE")

    /** [factor] is "FACE", "VOICE" or "UEBA" (typing). */
    fun startSetup(factor: String) = run { beginSetup(factor) }

    private suspend fun beginSetup(factor: String) {
        val link = withContext(io) { bank.startSetup(factor) }
        val api = amfaApi(link)
        val state = withContext(io) { api.enrollmentState() }
        if (state.enrolled[factor] == true) return finishSetup("That check is already set up.")
        if (factor == "OTP") {
            val t = withContext(io) { api.totpStart() }
            return go(Screen.AuthenticatorSetup(t.otpauthUri, t.secret))
        }
        go(when (factor) {
            "VOICE" -> Screen.VoiceSetup(state.consentRequired, state.consentVersion)
            "UEBA" -> Screen.TypingSetup(state.consentRequired, state.consentVersion, state.behaviour ?: throw ApiException(0, "The bank's typing setup is not available right now."))
            else -> Screen.FaceSetup(state.consentRequired, state.consentVersion)
        })
    }

    /** Confirms the authenticator app with the code it shows. */
    fun confirmAuthenticator(code: String) = run {
        val api = amfa ?: throw ApiException(0, "The setup session ended. Start it again.")
        withContext(io) { api.totpConfirm(code.trim()); api.enrollComplete() }
        finishSetup("Authenticator app set up. Payments that need a code will ask for the one it shows.")
    }

    /** Demo servers send no email: ask the bank for the code it was handed for this customer. */
    fun fetchDemoCode() = run {
        val code = withContext(io) { bank.demoCode() }
        demoCode = code
        if (code == null) error = "No code yet. The bank shows one a moment after it is requested."
    }

    /** Sends a fresh emailed code while a payment is waiting for one. */
    fun resendCode() {
        val v = screen as? Screen.Verify ?: return
        run {
            val api = amfa ?: throw ApiException(0, "The approval session ended. Start the payment again.")
            val session = withContext(io) { api.resendCode() }
            screen = Screen.Verify(v.actionId, session)
            demoCode = null
            notice = "We sent a new code."
        }
    }

    /** [frames] are the five JPEG data URLs of the setup script. */
    fun submitFaceSetup(frames: List<String>) {
        val s = screen as? Screen.FaceSetup ?: return
        setUp(s.consentRequired, s.consentVersion, "You're all set. Larger payments can now be approved with a quick face check.") { it.enrollFace(frames) }
    }

    /** [clips] are three base64 WAV recordings of the setup sentences. */
    fun submitVoiceSetup(clips: List<String>) {
        val s = screen as? Screen.VoiceSetup ?: return
        setUp(s.consentRequired, s.consentVersion, "Voice check set up.") { it.enrollVoice(clips) }
    }

    /** [samples] are the typed repetitions, [traces] the traced lines (phones only). */
    fun submitTypingSetup(samples: List<JSONArray>, traces: List<List<TracePoint>>) {
        val s = screen as? Screen.TypingSetup ?: return
        setUp(s.consentRequired, s.consentVersion, "Typing check set up.") { it.enrollBehaviour(samples, traces.map(::traceJson), "touch") }
    }

    /** Consents if needed, enrols with [send] (null means accepted, else AMFA's reason) and completes the setup link. */
    private fun setUp(consentRequired: Boolean, version: String, done: String, send: (AmfaApi) -> String?) = run {
        val api = amfa ?: throw ApiException(0, "The setup session ended. Start it again.")
        withContext(io) {
            if (consentRequired) api.consent(version)
            send(api)?.let { throw ApiException(422, setupReason(it)) }
            api.enrollComplete()
        }
        finishSetup(done)
    }

    private suspend fun finishSetup(message: String) {
        try { refresh() } catch (_: ApiException) { }
        go(Screen.Home)
        notice = message
    }

    // ------------------------------------------------------------ paying: the bank starts it, AMFA decides the checks

    /** [kind] is "transfer" (ref is a payee) or "bill" (ref is a biller). Shows the review screen with AMFA's plan. */
    fun startPayment(kind: String, ref: String, rupees: Double) = run {
        val started = withContext(io) { if (kind == "bill") bank.startBill(ref, rupees) else bank.startTransfer(ref, rupees) }
        pending = started
        screen = Screen.Review(started)
    }

    /** Sets the limit: the same checked flow as a payment (review, then AMFA's checks, then the bank applies it). */
    fun startNoCheck(rupees: Int) = run {
        val started = withContext(io) { bank.startNoCheck(rupees.toDouble()) }
        pending = started
        screen = Screen.Review(started)
    }

    fun removeNoCheck() = run {
        withContext(io) { bank.removeNoCheck() }
        refresh()
        go(Screen.Profile)
        notice = "Extra checks are back to normal."
    }

    /** "Confirm and pay": open AMFA's session if it asked for checks, otherwise let the bank settle it. */
    fun confirmPayment() {
        val started = pending ?: return
        run {
            val link = started.link ?: return@run settle(started.actionId)
            val api = amfaApi(link)
            val session = withContext(io) { api.session() }
            if (session.status == "PENDING" && session.challenge != null) screen = Screen.Verify(started.actionId, session)
            else settle(started.actionId)
        }
    }

    fun submitFace(frames: List<String>) = submit("FACE") { JSONObject().put("frames", JSONArray(frames)) }

    fun submitVoice(wavBase64: String) = submit("VOICE") { JSONObject().put("audio", wavBase64) }

    /** [sample] is the typed phrase's key events; [trace] the traced line when the check asked for one. */
    fun submitTyping(sample: JSONArray, trace: List<TracePoint>?) = submit("UEBA") {
        val p = JSONObject().put("keystrokeSample", sample).put("keystrokeInput", "touch")
        if (trace != null) p.put("shapeTrace", traceJson(trace)).put("shapePointerType", "touch")
        p
    }

    fun submitCode(code: String) = submit("OTP") { JSONObject().put("code", code.trim()) }

    private fun submit(factor: String, payload: () -> JSONObject) {
        val v = screen as? Screen.Verify ?: return
        val challenge = v.session.challenge ?: return
        run {
            val api = amfa ?: throw ApiException(0, "The approval session ended. Start the payment again.")
            val out = withContext(io) { api.submit(factor, challenge.id, payload()) }
            advance(v.actionId, out)
        }
    }

    private suspend fun advance(actionId: String, out: Submitted) {
        if (out.session.status != "PENDING") return settle(actionId)
        screen = Screen.Verify(actionId, out.session)
        demoCode = null
        error = if (out.factorStatus == "PASSED") null else (out.guidance ?: "That didn't work. Please try again.")
    }

    /** Asks the bank what became of the payment; the bank applies it only against AMFA's verified proof. */
    private suspend fun settle(actionId: String) {
        val done = withContext(io) { bank.complete(actionId) }
        try { refresh() } catch (_: ApiException) { }
        val a = done.action
        pending = null
        val m = me
        val wait = m?.noCheckPendingFrom?.let { ((it - System.currentTimeMillis() / 1000.0) / 3600).toLong() }
        go(Screen.Result(if (a.kind == "nocheck" && a.status == "DONE") noCheckOutcome(formatMoney(a.amountMinor), if (m?.noCheckPendingMinor != null) wait else null)
                         else outcomeFor(a.status, a.detail, formatMoney(a.amountMinor), a.payee, a.kind == "bill"), a))
    }

    fun cancelReview() {
        val s = pending ?: return go(Screen.Home)
        cancelAction(s.actionId)
    }

    fun cancelVerify() {
        val v = screen as? Screen.Verify ?: return
        cancelAction(v.actionId)
    }

    private fun cancelAction(actionId: String) = run {
        val isLimit = pending?.action?.kind == "nocheck"
        withContext(io) { try { bank.cancel(actionId) } catch (_: ApiException) { } }
        pending = null
        try { refresh() } catch (_: ApiException) { }
        go(if (isLimit) Screen.Profile else Screen.Home)
        notice = if (isLimit) "Cancelled. Nothing was changed." else "Payment cancelled. Nothing was sent."
    }

    fun refreshHome() = run { refresh() }

    // ------------------------------------------------------------ settings

    /** Returns false (and sets [error]) when the address is not usable. */
    fun saveBaseUrl(input: String): Boolean {
        val url = normalizeBaseUrl(input)
        if (url == null) {
            error = "That does not look like a server address. Example: http://192.168.1.20:8050"
            return false
        }
        store.baseUrl = url
        return true
    }

    fun testConnection(input: String) {
        if (!saveBaseUrl(input)) return
        run {
            withContext(io) { bank.ping() }
            notice = "Connected to ${store.baseUrl}"
        }
    }
}
