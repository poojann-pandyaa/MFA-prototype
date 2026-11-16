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
import com.adaptivemfa.app.data.KeystoreSigner
import com.adaptivemfa.app.data.BankApi
import com.adaptivemfa.app.data.Store
import com.adaptivemfa.app.data.UrlConnectionHttp
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.remember
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
                AppViewModel(store, BankApi(http, { store.baseUrl }, { store.deviceId }), { link -> AmfaApi(http, link, secure = true, pins = store) }, signer = KeystoreSigner(applicationContext))
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            MfaTheme {
                // a phone the bank has not verified for this account gets nothing but the verification (and sign-in, settings, results)
                val screen = vm.screen.let { s -> if (vm.untrusted && (s == Screen.Home || s is Screen.Pay || s is Screen.Amount || s == Screen.Activity ||
                    s == Screen.Profile || s == Screen.NoCheck || s == Screen.Helpers || s == Screen.Questions)) Screen.Device else s }
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
                            is Screen.AuthenticatorSetup -> AuthenticatorSetupScreen(vm, screen)
                            Screen.Home -> HomeScreen(vm)
                            is Screen.Pay -> PayScreen(vm, screen)
                            is Screen.Amount -> AmountScreen(vm, screen)
                            is Screen.Review -> ReviewScreen(vm, screen)
                            is Screen.Verify -> VerifyScreen(vm, screen)
                            is Screen.Result -> ResultScreen(vm, screen)
                            Screen.Activity -> ActivityScreen(vm)
                            Screen.Profile -> ProfileScreen(vm)
                            Screen.NoCheck -> NoCheckScreen(vm)
                            Screen.Helpers -> HelpersScreen(vm)
                            Screen.Device -> DeviceScreen(vm)
                            Screen.Questions -> QuestionsScreen(vm)
                            Screen.Settings -> SettingsScreen(vm)
                        }
                        // Any request in flight: a thin bar on top and taps swallowed, so no button (soft buttons and links included) can be
                        // pressed twice or look dead while the bank or AMFA answers. Screens with their own loader keep it.
                        if (vm.busy) {
                            Box(Modifier.fillMaxSize().clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) { })
                            LinearProgressIndicator(Modifier.fillMaxWidth().statusBarsPadding(), color = C.brand, trackColor = C.lineSoft)
                        }
                    }
                    if (tab != null) TabBar(tab) { vm.tab(it) }
                }
            }
        }
    }
}
