package com.adaptivemfa.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.adaptivemfa.app.audio.Microphone
import com.adaptivemfa.app.data.AmfaChallenge
import com.adaptivemfa.app.data.Behaviour
import com.adaptivemfa.app.logic.KeystrokeLog
import com.adaptivemfa.app.logic.Setup
import com.adaptivemfa.app.logic.TracePoint
import com.adaptivemfa.app.logic.nearStart
import com.adaptivemfa.app.logic.wavBase64
import kotlinx.coroutines.delay
import org.json.JSONArray

private fun nowMs() = SystemClock.elapsedRealtimeNanos() / 1e6

// ---------------------------------------------------------------- voice

/** Records one clip: Start, then Stop (after [minSeconds]); gives the base64 WAV or a message about why not. */
@Composable
private fun Recorder(minSeconds: Double, maxSeconds: Int, busy: Boolean, onClip: (String) -> Unit, onProblem: (String) -> Unit) {
    val context = LocalContext.current
    var recording by remember { mutableStateOf(false) }
    var level by remember { mutableStateOf(0.0) }
    var seconds by remember { mutableStateOf(0.0) }
    val mic = remember { Microphone() }
    var granted by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) }
    var denied by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it; denied = !it }

    fun finish() {
        recording = false
        val rec = mic.stop()
        if (rec.seconds < minSeconds) onProblem("That was too short. Please read all of it.") else onClip(wavBase64(rec.samples, rec.rate))
    }
    DisposableEffect(Unit) { onDispose { if (recording) mic.stop() } }
    LaunchedEffect(recording) {
        val started = SystemClock.elapsedRealtime()
        while (recording) {
            level = mic.level
            seconds = (SystemClock.elapsedRealtime() - started) / 1000.0
            if (seconds >= maxSeconds) finish()
            delay(80)
        }
        level = 0.0
    }
    if (!granted) {
        Note("brand", "info", if (denied) "Microphone access was refused. Allow it in the phone's app settings to use your voice." else "The voice check needs the microphone. Recordings go only to AMFA's servers and are not saved on the phone.")
        Btn("Allow microphone") { launcher.launch(Manifest.permission.RECORD_AUDIO) }
        return
    }
    Box(Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(5.dp)).background(C.lineSoft)) {
        Box(Modifier.fillMaxWidth(level.toFloat().coerceIn(0f, 1f)).height(10.dp).background(C.brand))
    }
    if (busy) { Busy("Checking your voice..."); return }
    Btn(if (recording) "Stop" else "Start recording", enabled = !recording || seconds >= minSeconds) {
        if (recording) finish() else try { mic.start(); recording = true } catch (e: IllegalStateException) { onProblem(e.message ?: "The microphone could not be opened.") }
    }
}

@Composable
fun VoiceSetupScreen(vm: AppViewModel, s: Screen.VoiceSetup) {
    var agreed by rememberSaveable { mutableStateOf(!s.consentRequired) }
    val clips = remember { mutableStateListOf<String>() }
    var problem by remember { mutableStateOf<String?>(null) }
    Page {
        TopBar(label = "Voice setup", onBack = { vm.back() })
        H1("Set up your voice")
        Lede("Read three short sentences aloud in your normal voice.")
        Messages(vm)
        problem?.let { Note("bad", "warn", it) }
        if (!agreed) ConsentCard("AMFA turns your voice into an encrypted voice template to confirm payments that look unusual and to detect impersonation. The recordings themselves are never stored. Demo Bank deletes the template if you ask.") { agreed = true }
        else {
            val i = clips.size
            CardBox { Muted("Sentence ${minOf(i + 1, 3)} of 3", small = true); Text(Setup.SENTENCES[minOf(i, 2)], color = C.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
            Recorder(Setup.MIN_CLIP_SECONDS, Setup.MAX_CLIP_SECONDS, vm.busy, onClip = {
                problem = null
                clips += it
                if (clips.size == Setup.SENTENCES.size) { vm.submitVoiceSetup(clips.toList()); clips.clear() }
            }, onProblem = { problem = it })
        }
    }
}

@Composable
fun ConsentCard(text: String, onAgree: () -> Unit) = CardBox {
    H3("Before we start")
    Muted(text)
    Btn("I agree, continue", onClick = onAgree)
}

@Composable
fun VoiceCheck(vm: AppViewModel, ch: AmfaChallenge) {
    var problem by remember { mutableStateOf<String?>(null) }
    problem?.let { Note("bad", "warn", it) }
    CardBox {
        H3("Voice check")
        Muted("Read these words aloud:")
        Text(ch.prompt ?: "", color = C.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    }
    Recorder(ch.minSeconds.toDouble(), ch.maxSeconds, vm.busy, onClip = { problem = null; vm.submitVoice(it) }, onProblem = { problem = it })
}

// ---------------------------------------------------------------- typing and tracing

/** The line to trace on a pad twice as wide as high (the web pad's shape); [onTrace] gets the finished stroke. */
@Composable
fun ShapePad(shape: List<Pair<Double, Double>>, onTrace: (List<TracePoint>) -> Unit) {
    val points = remember { mutableStateListOf<TracePoint>() }
    val guide = C.line; val start = C.ok; val end = C.bad; val ink = C.brand
    Canvas(
        Modifier.fillMaxWidth().aspectRatio(2f).clip(RoundedCornerShape(16.dp)).background(C.bg)
            .border(BorderStroke(1.5.dp, C.line), RoundedCornerShape(16.dp))
            .pointerInput(shape) {
                var active = false
                detectDragGestures(
                    onDragStart = { o ->
                        active = nearStart(o.x / size.width.toDouble(), o.y / size.height.toDouble(), shape)
                        points.clear()
                        if (active) points += TracePoint(o.x / size.width.toDouble(), o.y / size.height.toDouble(), nowMs())
                    },
                    onDrag = { change, _ ->
                        change.consume()
                        if (active && points.size < 5000) points += TracePoint(change.position.x / size.width.toDouble(), change.position.y / size.height.toDouble(), nowMs())
                    },
                    onDragEnd = { if (active) onTrace(points.toList()) },
                    onDragCancel = { points.clear() },
                )
            },
    ) {
        val w = size.width; val h = size.height
        for (i in 1 until shape.size) drawLine(guide, Offset((shape[i - 1].first * w).toFloat(), (shape[i - 1].second * h).toFloat()), Offset((shape[i].first * w).toFloat(), (shape[i].second * h).toFloat()), strokeWidth = (w * 0.02f).coerceAtLeast(10f), cap = StrokeCap.Round)
        shape.firstOrNull()?.let { drawCircle(start, (w * 0.018f).coerceAtLeast(9f), Offset((it.first * w).toFloat(), (it.second * h).toFloat())) }
        shape.lastOrNull()?.let { drawCircle(end, (w * 0.018f).coerceAtLeast(9f), Offset((it.first * w).toFloat(), (it.second * h).toFloat())) }
        for (i in 1 until points.size) drawLine(ink, Offset((points[i - 1].x * w).toFloat(), (points[i - 1].y * h).toFloat()), Offset((points[i].x * w).toFloat(), (points[i].y * h).toFloat()), strokeWidth = (w * 0.006f).coerceAtLeast(3f), cap = StrokeCap.Round)
    }
}

/** A text box that records typing rhythm; [onSubmit] gets the typed text and the key events when it equals [phrase]. */
@Composable
private fun PhraseBox(phrase: String, enabled: Boolean, resetKey: Int, onChange: (String) -> Unit, log: KeystrokeLog) {
    var text by remember(resetKey) { mutableStateOf("") }
    val interaction = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Box(Modifier.fillMaxWidth().height(52.dp).clip(RoundedCornerShape(14.dp)).background(C.card).border(BorderStroke(1.5.dp, if (focused) C.brand else C.line), RoundedCornerShape(14.dp)).padding(horizontal = 14.dp), contentAlignment = Alignment.CenterStart) {
        BasicTextField(
            text, { new -> if (new.length - text.length <= 3) { log.onText(new); text = new; onChange(new) } }, Modifier.fillMaxWidth(), enabled = enabled, singleLine = true,
            interactionSource = interaction, textStyle = TextStyle(color = C.ink, fontSize = 16.sp), cursorBrush = SolidColor(C.brand),
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, capitalization = KeyboardCapitalization.None, keyboardType = androidx.compose.ui.text.input.KeyboardType.Ascii),
        )
    }
}

@Composable
fun TypingSetupScreen(vm: AppViewModel, s: Screen.TypingSetup) {
    val b = s.behaviour
    var agreed by rememberSaveable { mutableStateOf(!s.consentRequired) }
    val log = remember { KeystrokeLog(::nowMs) }
    val samples = remember { mutableStateListOf<JSONArray>() }
    val traces = remember { mutableStateListOf<List<TracePoint>>() }
    var typed by remember { mutableStateOf("") }
    var reset by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf<String?>(null) }
    val tracing = samples.size >= b.repetitions && b.shape.isNotEmpty()
    Page {
        TopBar(label = "Typing setup", onBack = { vm.back() })
        H1(if (tracing) "Trace the line" else "Typing rhythm")
        Lede(if (tracing) "Drag from the green dot to the red dot, following the line. ${b.tracesRequired} times." else "Type the phrase below ${b.repetitions} times, pressing Done after each. The first one is practice, just type naturally. Type each letter yourself; turn off suggestions and corrections.")
        Messages(vm)
        note?.let { Note("warn", "info", it) }
        if (!agreed) ConsentCard("AMFA learns how you type and how you swipe to confirm payments that look unusual. Only timing and shape are kept, never what you type. Demo Bank deletes the template if you ask.") { agreed = true }
        else if (!tracing) {
            CardBox { Text(b.phrase, color = C.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()); Muted("${samples.size} of ${b.repetitions}", small = true, align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
            PhraseBox(b.phrase, !vm.busy, reset, { typed = it }, log)
            Btn("Done", enabled = typed.isNotEmpty() && !vm.busy, busy = vm.busy) {
                if (typed != b.phrase) { note = "The phrase doesn't match. Clear it and type it again."; log.take(); typed = ""; reset++; return@Btn }
                note = null
                samples += log.take(); typed = ""; reset++
                if (samples.size >= b.repetitions && b.shape.isEmpty()) vm.submitTypingSetup(samples.toList(), emptyList())
            }
        } else {
            ShapePad(b.shape) { if (it.size >= 10) { traces += it; if (traces.size >= b.tracesRequired) vm.submitTypingSetup(samples.toList(), traces.toList()) } }
            Muted("${traces.size} of ${b.tracesRequired}", small = true, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            if (vm.busy) Busy("Checking...")
        }
    }
}

@Composable
fun TypingCheck(vm: AppViewModel, ch: AmfaChallenge) {
    val phrase = ch.phrase ?: ""
    val log = remember { KeystrokeLog(::nowMs) }
    var typed by remember { mutableStateOf("") }
    var trace by remember { mutableStateOf<List<TracePoint>?>(null) }
    var reset by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf<String?>(null) }
    note?.let { Note("warn", "info", it) }
    CardBox {
        H3("Typing check")
        Muted("Type this phrase exactly, at your normal pace, with suggestions turned off:")
        Text(phrase, color = C.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    }
    PhraseBox(phrase, !vm.busy, reset, { typed = it }, log)
    if (ch.shape != null) {
        Muted("Then trace the line from the green dot to the red dot.")
        ShapePad(ch.shape) { if (it.size >= 10) trace = it }
    }
    Btn("Continue", enabled = typed.isNotEmpty() && !vm.busy, busy = vm.busy) {
        when {
            typed != phrase -> { note = "The phrase doesn't match. Clear it and type it again."; log.take(); typed = ""; reset++ }
            ch.shape != null && trace == null -> note = "Please trace the line as well."
            else -> { note = null; vm.submitTyping(log.take(), if (ch.shape != null) trace else null); typed = ""; reset++; trace = null }
        }
    }
}
