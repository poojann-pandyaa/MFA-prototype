package com.adaptivemfa.app.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/** The web bank's colour tokens (examples/demo_bank/static/app.css), light and dark; the phone's setting picks one. */
class Palette(
    val bg: Color, val card: Color, val ink: Color, val muted: Color, val line: Color, val lineSoft: Color,
    val brand: Color, val brandInk: Color, val brandSoft: Color, val brandDeep: Color,
    val ok: Color, val okSoft: Color, val warn: Color, val warnInk: Color, val warnSoft: Color, val warnLine: Color,
    val bad: Color, val badSoft: Color, val tones: List<Pair<Color, Color>>,
)

private val Light = Palette(
    bg = Color(0xFFF4F6F8), card = Color(0xFFFFFFFF), ink = Color(0xFF0F1B2D), muted = Color(0xFF4B5565),
    line = Color(0xFFE3E8EE), lineSoft = Color(0xFFEDF1F5),
    brand = Color(0xFF0B6E63), brandInk = Color(0xFFFFFFFF), brandSoft = Color(0xFFE3F2EF), brandDeep = Color(0xFF0F3D37),
    ok = Color(0xFF15803D), okSoft = Color(0xFFDCFCE7), warn = Color(0xFF92400E), warnInk = Color(0xFF5A3A06),
    warnSoft = Color(0xFFFEF3C7), warnLine = Color(0xFFF2C661), bad = Color(0xFFB42318), badSoft = Color(0xFFFEE4E2),
    tones = listOf(Color(0xFFFDE7C7) to Color(0xFF8A4B05), Color(0xFFDBE8FE) to Color(0xFF1E40AF),
        Color(0xFFE4DEFB) to Color(0xFF5B21B6), Color(0xFFE3F2EF) to Color(0xFF0B6E63)),
)

private val Dark = Palette(
    bg = Color(0xFF0E1318), card = Color(0xFF171E26), ink = Color(0xFFEDF1F5), muted = Color(0xFFA3ADBA),
    line = Color(0xFF2A3441), lineSoft = Color(0xFF222B35),
    brand = Color(0xFF4FC1B2), brandInk = Color(0xFF062B27), brandSoft = Color(0xFF143430), brandDeep = Color(0xFFBFEAE4),
    ok = Color(0xFF6FD39A), okSoft = Color(0xFF12321F), warn = Color(0xFFF0C060), warnInk = Color(0xFFF6DCA2),
    warnSoft = Color(0xFF33290F), warnLine = Color(0xFF8A6A1F), bad = Color(0xFFF2A29A), badSoft = Color(0xFF3D1A17),
    tones = listOf(Color(0xFF3B2A12) to Color(0xFFF5C27A), Color(0xFF172A4D) to Color(0xFFA9C4FB),
        Color(0xFF2B2245) to Color(0xFFC4B5FD), Color(0xFF143430) to Color(0xFF8FE0D4)),
)

val LocalPalette = staticCompositionLocalOf { Light }

/** `C.brand` etc. inside any composable. */
val C: Palette @Composable @ReadOnlyComposable get() = LocalPalette.current

@Composable
fun MfaTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val p = if (dark) Dark else Light
    val scheme = (if (dark) darkColorScheme() else lightColorScheme()).copy(
        primary = p.brand, onPrimary = p.brandInk, background = p.bg, onBackground = p.ink, surface = p.card, onSurface = p.ink,
        error = p.bad, outline = p.line,
    )
    CompositionLocalProvider(LocalPalette provides p) { MaterialTheme(colorScheme = scheme, content = content) }
}
