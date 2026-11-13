package com.demopay.app.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.demopay.app.data.Api
import com.demopay.app.data.ApiException
import com.demopay.app.data.Challenge
import com.demopay.app.data.DirectoryItem
import com.demopay.app.data.Lookup
import com.demopay.app.data.Me
import com.demopay.app.data.Payment
import com.demopay.app.data.PaymentRow
import com.demopay.app.data.Persona
import com.demopay.app.data.RiskRequest
import com.demopay.app.data.Store
import com.demopay.app.data.UrlConnectionHttp
import com.demopay.app.data.XRay
import com.demopay.app.logic.Signals
import com.demopay.app.logic.isValidBaseUrl
import com.demopay.app.logic.normaliseBaseUrl
import com.demopay.app.signals.PhoneSignals
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

sealed interface Screen {
    data object Login : Screen
    data object Home : Screen
    data object Send : Screen
    data object Settings : Screen
    data class Pay(val id: Int) : Screen
}

class AppViewModel(app: Application) : AndroidViewModel(app) {
    val store = Store(app)
    val phone = PhoneSignals(app)
    private val api = Api(UrlConnectionHttp(), { store.baseUrl }, { store.token }, { store.deviceId })

    var screen by mutableStateOf<Screen>(if (store.token != null) Screen.Home else Screen.Login)
        private set
    var me by mutableStateOf<Me?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    var info by mutableStateOf<String?>(null)
    var rows by mutableStateOf<List<PaymentRow>>(emptyList())
        private set
    var personas by mutableStateOf<List<Persona>>(emptyList())
        private set
    var directory by mutableStateOf<List<DirectoryItem>>(emptyList())
        private set
    var lookup by mutableStateOf<Lookup?>(null)
        private set
    var payment by mutableStateOf<Payment?>(null)
        private set
    var challenge by mutableStateOf<Challenge?>(null)
        private set
    var stepMessage by mutableStateOf<String?>(null)
    var xray by mutableStateOf<XRay?>(null)
        private set
    var paidBalance by mutableStateOf<Double?>(null)
        private set
    var useRealSignals by mutableStateOf(store.useRealSignals)
        private set
    var baseUrl by mutableStateOf(store.baseUrl)
        private set
    var phoneReport by mutableStateOf(phone.report())
        private set

    init {
        phone.start()
        if (store.token != null) refreshAll()
    }

    override fun onCleared() {
        phone.stop()
    }

    private fun guarded(block: suspend () -> Unit) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                block()
            } catch (e: ApiException) {
                if (e.status == 401 && store.token != null) signOut() else error = e.message
            } catch (e: Exception) {
                error = e.message ?: "Something went wrong"
            } finally {
                busy = false
            }
        }
    }

    private suspend fun <T> io(f: () -> T): T = withContext(Dispatchers.IO) { f() }

    // ---- navigation ------------------------------------------------------------------------------------------
    fun go(s: Screen) { error = null; screen = s }
    fun back() {
        when (screen) {
            is Screen.Pay, Screen.Send, Screen.Settings -> { go(Screen.Home); refreshAll() }
            else -> Unit
        }
    }

    // ---- account ------------------------------------------------------------------------------------------------
    fun authenticate(username: String, password: String, signUp: Boolean) = guarded {
        val token = io { if (signUp) api.signup(username.trim(), password) else api.login(username.trim(), password) }
        store.token = token
        screen = Screen.Home
        loadAll()
    }

    fun signOut() {
        store.token = null
        me = null; payment = null; challenge = null; xray = null; rows = emptyList()
        screen = Screen.Login
    }

    fun refreshAll() = guarded { loadAll() }

    private suspend fun loadAll() {
        val m = io { api.me() }
        me = m
        rows = io { api.activity() }
        if (m.demo) {
            personas = io { api.personas() }
            directory = io { api.directory() }
        }
    }

    fun loadPersona(name: String) = guarded {
        info = io { api.loadPersona(name) }.ifEmpty { "Loaded $name." }
        loadAll()
    }

    fun resetDemo() = guarded {
        io { api.reset() }
        info = "Demo reset."
        loadAll()
    }

    // ---- sending ----------------------------------------------------------------------------------------------
    fun lookupPayee(vpa: String) {
        if (vpa.length < 3) { lookup = null; return }
        viewModelScope.launch {
            lookup = try { io { api.lookup(vpa) } } catch (e: Exception) { null }
        }
    }

    /** Real readings from this phone (if allowed in Settings), overridden by whatever the presenter set. */
    fun effectiveSignals(manual: Signals): Signals =
        if (useRealSignals) phone.snapshot().overriddenBy(manual) else manual

    fun submit(req: RiskRequest) = guarded {
        val p = io { api.risk(req) }
        openLoaded(p)
    }

    fun openPayment(id: Int) = guarded { openLoaded(io { api.payment(id) }) }

    private suspend fun openLoaded(p: Payment) {
        payment = p
        challenge = null
        stepMessage = null
        paidBalance = null
        screen = Screen.Pay(p.id)
        xray = if (me?.demo == true) try { io { api.xray(p.id) } } catch (e: Exception) { null } else null
    }

    // ---- steps --------------------------------------------------------------------------------------------------
    fun startStep() {
        val p = payment ?: return
        val n = p.currentStep ?: return
        if (challenge != null) return
        guarded { challenge = io { api.startStep(p.id, n) } }
    }

    fun answerStep(body: JSONObject) {
        val p = payment ?: return
        val n = p.currentStep ?: return
        val c = challenge ?: return
        guarded {
            body.put("nonce", c.nonce)
            val r = io { api.answerStep(p.id, n, body) }
            stepMessage = if (r.retry) (r.message + (r.attemptsLeft?.let { " $it attempt(s) left." } ?: "")).trim() else if (r.wait) r.message else null
            if (r.passed || r.blocked) challenge = null
            if (!r.retry && !r.wait) payment = io { api.payment(p.id) }
        }
    }

    fun cancelPayment() {
        val p = payment ?: return
        guarded { payment = io { api.cancel(p.id) }; challenge = null }
    }

    fun complete(pin: String) {
        val p = payment ?: return
        guarded {
            paidBalance = io { api.complete(p.id, pin) }
            payment = io { api.payment(p.id) }
            me = io { api.me() }
        }
    }

    // ---- settings -----------------------------------------------------------------------------------------------
    fun saveBaseUrl(input: String): Boolean {
        if (!isValidBaseUrl(input)) { error = "Enter an address like http://10.0.2.2:8070"; return false }
        store.baseUrl = normaliseBaseUrl(input)
        baseUrl = store.baseUrl
        error = null
        info = "Server address saved."
        return true
    }

    fun changeUseRealSignals(v: Boolean) {
        useRealSignals = v
        store.useRealSignals = v
    }

    fun onPhonePermissionResult() {
        phone.start()
        phoneReport = phone.report()
    }

    fun refreshPhoneReport() { phoneReport = phone.report() }
}
