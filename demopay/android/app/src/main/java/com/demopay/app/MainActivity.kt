package com.demopay.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import com.demopay.app.ui.AppViewModel
import com.demopay.app.ui.DemoPayTheme
import com.demopay.app.ui.HomeScreen
import com.demopay.app.ui.LoginScreen
import com.demopay.app.ui.PayScreen
import com.demopay.app.ui.Screen
import com.demopay.app.ui.SendScreen
import com.demopay.app.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            DemoPayTheme {
                BackHandler(enabled = vm.screen !is Screen.Home && vm.screen !is Screen.Login) {
                    if (vm.screen is Screen.Settings && vm.me == null) vm.go(Screen.Login) else vm.back()
                }
                when (vm.screen) {
                    Screen.Login -> LoginScreen(vm)
                    Screen.Home -> HomeScreen(vm)
                    Screen.Send -> SendScreen(vm)
                    Screen.Settings -> SettingsScreen(vm)
                    is Screen.Pay -> PayScreen(vm)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        vm.refreshPhoneReport()
    }
}
