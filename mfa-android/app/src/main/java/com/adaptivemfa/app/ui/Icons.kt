package com.adaptivemfa.app.ui

import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** The web bank's stroke icons (examples/demo_bank/static/js/ui.js), same paths, 24 x 24, 2 px round strokes. */
private val PATHS = mapOf(
    "back" to "M15 18l-6-6 6-6", "close" to "M6 6l12 12M18 6L6 18", "chevron" to "M9 6l6 6-6 6",
    "send" to "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z", "bill" to "M6 2h12v20l-3-2-3 2-3-2-3 2V2zM9 7h6M9 11h6M9 15h3",
    "payee" to "M9 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21c1-4 4-6 7-6s6 2 7 6M19 8v6M16 11h6",
    "shield" to "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3z",
    "shieldCheck" to "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM8.5 12.2l2.4 2.4 4.6-4.8",
    "shieldClock" to "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM12 8v4l2.5 1.5",
    "shieldAlert" to "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM12 8v4M12 15.5v.5",
    "home" to "M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V11z", "activity" to "M3 12h4l3-8 4 16 3-8h4",
    "profile" to "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6", "check" to "M5 12.5l4.5 4.5L19 7.5",
    "lock" to "M5 11h14v10H5zM8 11V8a4 4 0 0 1 8 0v3",
    "warn" to "M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17v.5",
    "info" to "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v4M12 16v.5", "plus" to "M12 5v14M5 12h14",
    "eye" to "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "eyeOff" to "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7c1.6 0 3-.4 4.3-1",
    "phone" to "M5 2.5h14v19H5zM10 18.5h4", "mail" to "M3 5h18v14H3zM3 6l9 7 9-7",
    "face" to "M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M12 8a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM8.5 16.5c.9-1.4 2-2 3.5-2s2.6.6 3.5 2",
    "bolt" to "M13 2L4 14h7l-1 8 9-12h-7l1-8z", "drop" to "M12 22c4 0 7-3 7-7 0-5-7-13-7-13S5 10 5 15c0 4 3 7 7 7z",
    "search" to "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5",
    "bank" to "M3 10h18M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18M12 3l9 5H3l9-5z",
    "ban" to "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8", "key" to "M15 7a4 4 0 1 1-3.5 6L4 20.5V17h3v-3h3l1.5-1.5A4 4 0 0 1 15 7z",
    "arrowLeft" to "M19 12H5M11 6l-6 6 6 6", "arrowRight" to "M5 12h14M13 6l6 6-6 6", "del" to "M21 5H9l-7 7 7 7h12a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1zM17 9l-6 6M11 9l6 6",
)

private val cache = HashMap<String, ImageVector>()

private fun vector(name: String): ImageVector = cache.getOrPut(name) {
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).addPath(
        addPathNodes(PATHS[name] ?: error("no icon $name")),
        stroke = SolidColor(Color.Black), strokeLineWidth = 2f, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round,
    ).build()
}

@Composable
fun Ic(name: String, tint: Color = C.ink, size: Dp = 22.dp, label: String? = null, modifier: Modifier = Modifier) {
    val v = remember(name) { vector(name) }
    Icon(v, contentDescription = label, tint = tint, modifier = modifier.size(size))
}
