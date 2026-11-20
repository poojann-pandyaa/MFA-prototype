package com.adaptivemfa.app.ui

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.logic.slideCompleted
import kotlin.math.roundToInt

/**
 * A slide-to-confirm control for something that cannot be undone. The thumb follows the finger; let go past
 * [com.adaptivemfa.app.logic.SLIDE_CONFIRM_AT] of the track and [onComplete] runs, let go sooner and it springs back.
 * Change [resetKey] to send the thumb back to the start (for example when the confirmation popup was dismissed).
 * TalkBack users, who cannot drag, get the same action as a double tap.
 */
@Composable
fun SlideToCancel(label: String, onComplete: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, resetKey: Any? = null) {
    val thumb = 56.dp
    val gap = 4.dp                                                  // between the thumb and the track's edge
    val (thumbPx, gapPx) = with(LocalDensity.current) { thumb.toPx() to gap.toPx() }
    var trackPx by remember { mutableFloatStateOf(0f) }
    val travel = (trackPx - thumbPx - 2 * gapPx).coerceAtLeast(1f)
    var fraction by remember { mutableFloatStateOf(0f) }          // 0 = thumb at the start, 1 = at the end
    var dragging by remember { mutableStateOf(false) }
    val shown by animateFloatAsState(fraction, if (dragging) snap() else spring(stiffness = Spring.StiffnessMedium), label = "slide")
    LaunchedEffect(resetKey) { fraction = 0f }

    Box(
        modifier.fillMaxWidth().height(thumb + gap * 2).alpha(if (enabled) 1f else 0.5f)
            .clip(RoundedCornerShape(20.dp)).background(C.badSoft).border(BorderStroke(1.5.dp, C.bad), RoundedCornerShape(20.dp))
            .onSizeChanged { trackPx = it.width.toFloat() }
            .semantics { if (enabled) onClick(label = label) { onComplete(); true } },
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(
            label, color = C.bad, fontSize = 15.sp, fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.Center).alpha((1f - shown * 1.4f).coerceIn(0f, 1f)),
        )
        Box(
            Modifier.padding(gap).offset { IntOffset((shown * travel).roundToInt(), 0) }.size(thumb).clip(RoundedCornerShape(16.dp)).background(C.bad)
                .draggable(
                    orientation = Orientation.Horizontal, enabled = enabled,
                    state = rememberDraggableState { d -> fraction = (fraction + d / travel).coerceIn(0f, 1f) },
                    onDragStarted = { dragging = true },
                    onDragStopped = {
                        dragging = false
                        if (slideCompleted(fraction)) { fraction = 1f; onComplete() } else fraction = 0f
                    },
                ),
            contentAlignment = Alignment.Center,
        ) { Ic("chevron", Color.White, 26.dp) }
    }
}
