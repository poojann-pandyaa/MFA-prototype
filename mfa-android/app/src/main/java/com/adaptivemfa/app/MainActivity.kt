package com.adaptivemfa.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.adaptivemfa.app.ui.AppViewModel
import com.adaptivemfa.app.ui.DashboardScreen
import com.adaptivemfa.app.ui.EnrollScreen
import com.adaptivemfa.app.ui.FaceCheckScreen
import com.adaptivemfa.app.ui.LoginScreen
import com.adaptivemfa.app.ui.MfaTheme
import com.adaptivemfa.app.ui.Screen
import com.adaptivemfa.app.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            MfaTheme {
                val screen = vm.screen
                BackHandler(enabled = screen != Screen.Login && screen != Screen.Dashboard) { vm.back() }
                when (screen) {
                    Screen.Login -> LoginScreen(vm)
                    Screen.Enroll -> EnrollScreen(vm)
                    is Screen.FaceCheck -> FaceCheckScreen(vm, screen)
                    Screen.Dashboard -> DashboardScreen(vm)
                    Screen.Settings -> SettingsScreen(vm)
                }
            }
        }
    }
}
