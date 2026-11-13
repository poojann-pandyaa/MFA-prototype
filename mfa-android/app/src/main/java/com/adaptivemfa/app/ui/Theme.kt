package com.adaptivemfa.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Brand = Color(0xFF2563EB)
val BrandDark = Color(0xFF1E3A8A)
val Good = Color(0xFF16A34A)
val Warn = Color(0xFFD97706)
val Bad = Color(0xFFDC2626)

private val colors = lightColorScheme(
    primary = Brand,
    onPrimary = Color.White,
    secondary = BrandDark,
    background = Color(0xFFF3F4F6),
    surface = Color.White,
    error = Bad,
)

@Composable
fun MfaTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = colors, content = content)
}

fun riskColor(risk: String): Color = when (risk.uppercase()) {
    "LOW" -> Good
    "MEDIUM" -> Warn
    "HIGH" -> Bad
    else -> Color.Gray
}
