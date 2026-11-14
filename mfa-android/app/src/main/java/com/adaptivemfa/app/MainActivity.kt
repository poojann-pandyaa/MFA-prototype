package com.adaptivemfa.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.adaptivemfa.app.data.AmfaApi
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.Store
import com.adaptivemfa.app.data.UrlConnectionHttp
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.ui.Modifier
import com.adaptivemfa.app.ui.*

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels {
        viewModelFactory {
            initializer {
                val store = Store(applicationContext)
                val http = UrlConnectionHttp()
                AppViewModel(store, BankApi(http, { store.baseUrl }, { store.deviceId }), { link -> AmfaApi(http, link) })
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            MfaTheme {
                val screen = vm.screen
                val tab = when (screen) { Screen.Home -> "home"; is Screen.Pay -> "pay"; Screen.Activity -> "activity"; Screen.Profile -> "profile"; else -> null }
                BackHandler(enabled = screen != Screen.Welcome && screen != Screen.Home) { vm.back() }
                Column(Modifier.fillMaxSize().then(if (tab == null) Modifier.navigationBarsPadding() else Modifier)) {
                    androidx.compose.foundation.layout.Box(Modifier.weight(1f)) {
                        when (screen) {
                            Screen.Welcome -> WelcomeScreen(vm)
                            Screen.SignIn -> SignInScreen(vm)
                            Screen.SignUp -> SignUpScreen(vm)
                            Screen.Setup -> SetupScreen(vm)
                            is Screen.FaceSetup -> FaceSetupScreen(vm, screen)
                            is Screen.VoiceSetup -> VoiceSetupScreen(vm, screen)
                            is Screen.TypingSetup -> TypingSetupScreen(vm, screen)
                            Screen.Home -> HomeScreen(vm)
                            is Screen.Pay -> PayScreen(vm, screen)
                            is Screen.Amount -> AmountScreen(vm, screen)
                            is Screen.Review -> ReviewScreen(vm, screen)
                            is Screen.Verify -> VerifyScreen(vm, screen)
                            is Screen.Result -> ResultScreen(vm, screen)
                            Screen.Activity -> ActivityScreen(vm)
                            Screen.Profile -> ProfileScreen(vm)
                            Screen.Settings -> SettingsScreen(vm)
                        }
                    }
                    if (tab != null) TabBar(tab) { vm.tab(it) }
                }
            }
        }
    }
}
