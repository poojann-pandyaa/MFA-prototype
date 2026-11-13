package com.demopay.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Navy = Color(0xFF14325C)
val Teal = Color(0xFF1B7F8C)
val Slate = Color(0xFF5A6B80)
val Page = Color(0xFFEEF2F7)
val Amber = Color(0xFFFFF4DE)
val AmberText = Color(0xFF7A4B00)
val Danger = Color(0xFFA32020)
val DangerBg = Color(0xFFFDECEC)
val Good = Color(0xFF2E7D4F)
val GoodBg = Color(0xFFE6F4EA)

private val Colors = lightColorScheme(
    primary = Teal, onPrimary = Color.White, secondary = Navy, onSecondary = Color.White,
    background = Page, surface = Color.White, onSurface = Color(0xFF1B2A3D), error = Danger,
    surfaceVariant = Color(0xFFE3E9F1), outline = Color(0xFFB7C2D0),
)

@Composable
fun DemoPayTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Colors, content = content)
}
