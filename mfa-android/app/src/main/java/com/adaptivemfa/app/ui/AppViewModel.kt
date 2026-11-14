package com.adaptivemfa.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.adaptivemfa.app.data.Api
import com.adaptivemfa.app.data.ApiException
import com.adaptivemfa.app.data.HistoryItem
import com.adaptivemfa.app.data.Settings
import com.adaptivemfa.app.data.Step1Result
import com.adaptivemfa.app.logic.normalizeBaseUrl
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

sealed interface Screen {
    data object Login : Screen
    data object Enroll : Screen
    /** Step 2: [sessionId] proves step 1 passed; it is single use and expires after about 90 s. */
    data class FaceCheck(val username: String, val sessionId: String, val challengeType: String, val riskScore: String) : Screen
    data object Dashboard : Screen
    data object Settings : Screen
}

class AppViewModel(
    val store: Settings,
    private val api: Api,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    var screen by mutableStateOf<Screen>(if (store.token != null) Screen.Dashboard else Screen.Login)
        private set
    /** Where Settings returns to. */
    private var beforeSettings: Screen = Screen.Login

    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    var notice by mutableStateOf<String?>(null)

    var username by mutableStateOf(store.username ?: "")
        private set
    var history by mutableStateOf<List<HistoryItem>>(emptyList())
        private set

    fun go(to: Screen) {
        error = null
        notice = null
        if (to == Screen.Settings && screen != Screen.Settings) beforeSettings = screen
        screen = to
    }

    fun back() {
        when (screen) {
            Screen.Settings -> go(beforeSettings)
            Screen.Enroll, is Screen.FaceCheck -> go(Screen.Login)
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
                error = e.message
            } catch (e: Exception) {
                error = e.message ?: "Something went wrong."
            } finally {
                busy = false
            }
        }
    }

    private suspend fun signedIn(user: String, token: String, message: String) {
        store.token = token
        store.username = user
        username = user
        go(Screen.Dashboard)
        notice = message
        loadHistory(token)
    }

    fun login(user: String, password: String) = run {
        val u = user.trim()
        when (val r = withContext(io) { api.loginStep1(u, password) }) {
            is Step1Result.LoggedIn -> signedIn(u, r.accessToken, "Signed in with your password only: ${r.riskScore} risk (known device).")
            is Step1Result.FaceCheckRequired -> go(Screen.FaceCheck(u, r.sessionId, r.challengeType, r.riskScore))
        }
    }

    /** [frames] are JPEG data URLs from the burst. */
    fun submitFaceCheck(frames: List<String>) {
        val s = screen as? Screen.FaceCheck ?: return
        run {
            val token = try {
                withContext(io) { api.loginStep2(s.username, s.sessionId, frames) }
            } catch (e: ApiException) {
                // The server uses up the session on every attempt, so a retry needs a fresh step 1.
                go(Screen.Login)
                throw ApiException(e.status, e.message + " Log in again to get a new challenge.")
            }
            signedIn(s.username, token, "Face check passed. This phone is now a known device for ${s.username}.")
        }
    }

    fun enroll(user: String, password: String, faceDataUrl: String) = run {
        val u = user.trim()
        val token = withContext(io) { api.enroll(u, password, faceDataUrl) }
        signedIn(u, token, "Account created. Your next login from this phone will ask for a face check once.")
    }

    fun refreshHistory() {
        val token = store.token ?: return
        run { loadHistory(token) }
    }

    private suspend fun loadHistory(token: String) {
        try {
            history = withContext(io) { api.history(token) }
        } catch (e: ApiException) {
            if (e.status == 401) {
                logout()
                throw ApiException(401, "Your session expired. Please log in again.")
            }
            throw e
        }
    }

    fun logout() {
        store.token = null
        history = emptyList()
        go(Screen.Login)
    }

    /** Returns false (and sets [error]) when the address is not usable. */
    fun saveBaseUrl(input: String): Boolean {
        val url = normalizeBaseUrl(input)
        if (url == null) {
            error = "That does not look like a server address. Example: http://192.168.1.20:8000"
            return false
        }
        store.baseUrl = url
        return true
    }

    fun testConnection(input: String) {
        if (!saveBaseUrl(input)) return
        run {
            withContext(io) { api.ping() }
            notice = "Connected to ${store.baseUrl}"
        }
    }
}
