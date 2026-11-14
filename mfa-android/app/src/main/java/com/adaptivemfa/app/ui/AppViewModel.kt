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
import com.adaptivemfa.app.data.Me
import com.adaptivemfa.app.data.SessionState
import com.adaptivemfa.app.data.Settings
import com.adaptivemfa.app.data.Submitted
import com.adaptivemfa.app.logic.faceSetupReason
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.normalizeBaseUrl
import com.adaptivemfa.app.logic.reachableAmfaBase
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

sealed interface Screen {
    data object Login : Screen
    data object Register : Screen
    /** AMFA's face enrolment: [consentRequired] says whether the customer still has to agree to the notice first. */
    data class FaceSetup(val consentRequired: Boolean, val consentVersion: String) : Screen
    data object Dashboard : Screen
    /** One AMFA approval in progress; [session] carries the current challenge (face, code or something unsupported). */
    data class Verify(val actionId: String, val session: SessionState) : Screen
    data object Settings : Screen
}

class AppViewModel(
    val store: Settings,
    private val bank: BankApi,
    private val amfaFor: (AmfaLink) -> AmfaApi,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    var screen by mutableStateOf<Screen>(Screen.Login)
        private set
    private var beforeSettings: Screen = Screen.Login

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

    private var amfa: AmfaApi? = null

    fun go(to: Screen) {
        error = null
        notice = null
        if (to == Screen.Settings && screen != Screen.Settings) beforeSettings = screen
        screen = to
    }

    fun back() {
        when (screen) {
            Screen.Settings -> go(beforeSettings)
            Screen.Register -> go(Screen.Login)
            is Screen.FaceSetup -> { go(Screen.Dashboard); notice = "You can set up the face check later from the home screen." }
            is Screen.Verify -> cancelVerify()
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
                if (e.status == 401 && screen == Screen.Dashboard) signOut("Your session expired. Please sign in again.") else error = e.message
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
        go(Screen.Dashboard)
    }

    // ------------------------------------------------------------ account

    fun login(user: String, password: String) = run {
        val u = user.trim()
        withContext(io) { bank.login(u, password) }
        enter(u)
    }

    fun register(user: String, name: String, phone: String, email: String, password: String) = run {
        val u = user.trim().lowercase()
        withContext(io) { bank.register(u, name.trim(), phone.trim(), email.trim(), password) }
        store.username = u
        username = u
        refresh()
        beginFaceSetup()
    }

    fun logout() = run { withContext(io) { try { bank.logout() } catch (_: ApiException) { } }; signOut(null) }

    private fun signOut(message: String?) {
        me = null
        activity = emptyList()
        amfa = null
        go(Screen.Login)
        error = message
    }

    // ------------------------------------------------------------ face setup (AMFA enrolment)

    fun startFaceSetup() = run { beginFaceSetup() }

    private suspend fun beginFaceSetup() {
        val link = withContext(io) { bank.startFaceSetup() }
        val api = amfaApi(link)
        val state = withContext(io) { api.enrollmentState() }
        if (state.faceEnrolled) return finishSetup("Your face check is already set up.")
        go(Screen.FaceSetup(state.consentRequired, state.consentVersion))
    }

    /** [frames] are the five JPEG data URLs of the setup script. */
    fun submitFaceSetup(frames: List<String>) {
        val s = screen as? Screen.FaceSetup ?: return
        run {
            val api = amfa ?: throw ApiException(0, "The setup session ended. Start the face setup again.")
            withContext(io) {
                if (s.consentRequired) api.consent(s.consentVersion)
                api.enrollFace(frames)?.let { throw ApiException(422, faceSetupReason(it)) }
                api.enrollComplete()
            }
            finishSetup("Face check set up. Larger payments can now be approved with a quick face check.")
        }
    }

    private suspend fun finishSetup(message: String) {
        try { refresh() } catch (_: ApiException) { }
        go(Screen.Dashboard)
        notice = message
    }

    // ------------------------------------------------------------ paying: the bank starts it, AMFA decides the checks

    fun sendMoney(payeeRef: String, rupees: Double) = run {
        val started = withContext(io) { bank.startTransfer(payeeRef, rupees) }
        val link = started.link
        if (link == null) return@run settle(started.actionId)
        val api = amfaApi(link)
        val session = withContext(io) { api.session() }
        if (session.status == "PENDING" && session.challenge != null) screen = Screen.Verify(started.actionId, session)
        else settle(started.actionId)
    }

    fun submitFace(frames: List<String>) = submit("FACE") { JSONObject().put("frames", JSONArray(frames)) }

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
        error = if (out.factorStatus == "PASSED") null else (out.guidance ?: "That didn't work. Please try again.")
    }

    /** Asks the bank what became of the payment; the bank applies it only against AMFA's verified proof. */
    private suspend fun settle(actionId: String) {
        val done = withContext(io) { bank.complete(actionId) }
        try { refresh() } catch (_: ApiException) { }
        go(Screen.Dashboard)
        val text = when (done.status) {
            "DONE" -> "Payment sent. Your balance is ${formatMoney(done.balanceMinor)}."
            "UNDER_REVIEW" -> "The payment is waiting for a bank review. You will see the result under Activity."
            "DECLINED" -> "The payment was declined. ${done.detail ?: ""}".trim()
            "EXPIRED" -> "The approval timed out. Start the payment again."
            "CANCELLED" -> "The payment was cancelled."
            else -> done.detail ?: "The payment could not be completed (${done.status})."
        }
        if (done.status == "DONE" || done.status == "UNDER_REVIEW") notice = text else error = text
    }

    fun cancelVerify() {
        val v = screen as? Screen.Verify ?: return
        run {
            withContext(io) { try { bank.cancel(v.actionId) } catch (_: ApiException) { } }
            try { refresh() } catch (_: ApiException) { }
            go(Screen.Dashboard)
            notice = "Payment cancelled."
        }
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
