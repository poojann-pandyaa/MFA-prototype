package com.adaptivemfa.app.ui

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.camera.CameraPermissionGate
import com.adaptivemfa.app.camera.FaceCamera
import com.adaptivemfa.app.camera.FrameGrabber
import com.adaptivemfa.app.data.AmfaChallenge
import com.adaptivemfa.app.logic.Burst
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.FaceSetup
import com.adaptivemfa.app.logic.jpegDataUrl
import com.adaptivemfa.app.logic.registrationProblem
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

// ---------------------------------------------------------------- welcome

@Composable
fun WelcomeScreen(vm: AppViewModel) = Page {
    BrandMark()
    Box(Modifier.fillMaxWidth().padding(vertical = 8.dp), contentAlignment = Alignment.Center) { HeroArt() }
    H1("Banking that knows it's really you")
    Lede("Everyday payments stay one tap. Anything unusual gets a quick face or code check, so nobody else can move your money.")
    Btn("Open an account") { vm.go(Screen.SignUp) }
    Btn("I already bank here", BtnKind.Secondary) { vm.go(Screen.SignIn) }
    Muted("Bank: ${vm.store.baseUrl}", small = true, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    LinkText("Settings", small = true, modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.go(Screen.Settings) }
}

// ---------------------------------------------------------------- sign in / sign up

@Composable
fun SignInScreen(vm: AppViewModel) {
    var user by rememberSaveable { mutableStateOf(vm.username) }
    var password by rememberSaveable { mutableStateOf("") }
    Page {
        TopBar(onBack = { vm.back() })
        H1("Welcome back")
        Lede("Sign in to your Demo Bank account.")
        Messages(vm)
        Field("Username", user, { user = it }, enabled = !vm.busy)
        Field("Password", password, { password = it }, enabled = !vm.busy, password = true)
        Btn("Sign in", enabled = user.isNotBlank() && password.isNotEmpty(), busy = vm.busy) { vm.signIn(user, password) }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            Muted("New here? "); LinkText("Open an account") { vm.go(Screen.SignUp) }
        }
    }
}

@Composable
fun SignUpScreen(vm: AppViewModel) {
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    var user by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var touched by rememberSaveable { mutableStateOf(false) }
    val digits = phone.filter(Char::isDigit)
    val problem = registrationProblem(user.trim().lowercase(), name, digits, email, password, password)
    Page {
        TopBar(onBack = { vm.back() })
        H1("Open an account")
        Lede("It takes about a minute. Use an email you can open on this phone: your one-time codes go there.")
        Messages(vm)
        Field("Full name", name, { name = it }, enabled = !vm.busy)
        Field("Email (we send your one-time codes here)", email, { email = it }, enabled = !vm.busy, keyboard = KeyboardType.Email, placeholder = "you@example.com")
        Field("Mobile number", phone, { phone = it.filter(Char::isDigit).take(10) }, enabled = !vm.busy, keyboard = KeyboardType.Phone, prefix = "+91", placeholder = "98123 45678")
        Field("Username", user, { user = it }, enabled = !vm.busy)
        Field("Password", password, { password = it }, enabled = !vm.busy, password = true,
            error = if (touched && problem != null) problem else null)
        Btn("Open account", busy = vm.busy) {
            touched = true
            if (problem == null) vm.signUp(user, name, "+91$digits", email, password)
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            Muted("Already a customer? "); LinkText("Sign in") { vm.go(Screen.SignIn) }
        }
    }
}

// ---------------------------------------------------------------- "Let's protect your account"

@Composable
fun SetupScreen(vm: AppViewModel) {
    val me = vm.me
    val byEmail = me?.codeTo?.contains("@") == true
    Page(scroll = true) {
        TopBar(label = "Step 1 of 2 · about 1 minute")
        StepsBar(1, 2)
        H1("Let's protect your account")
        Lede("Two things, then you can start banking. We'll only ask for a check when a payment looks unusual.")
        Messages(vm)
        ListCard(listOf(
            { Item({ Tile(if (byEmail) "mail" else "phone") }, if (byEmail) "Codes by email" else "SMS codes", { SubText("Sent to ${me?.codeTo ?: ""}") }, { Chip("Ready", "ok") }) },
            { Item({ Tile("face") }, "Your face", { SubText("Five quick pictures, about 30 seconds") }, { Chip("Next") }) },
        ))
        Note("brand", "lock", "Your photos never reach the bank. Only an encrypted template is kept, and Demo Bank will delete it if you ask.")
        Btn("Set up my face", busy = vm.busy) { vm.startFaceSetup() }
        Muted("You'll confirm your consent on the next screen. You can also do this later from Profile.", small = true, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        LinkText("Not now", modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.go(Screen.Home) }
    }
}

// ---------------------------------------------------------------- camera: a circle with a progress ring and a turn arrow

@Composable
private fun FaceCircle(grabber: FrameGrabber, prompt: String, progress: Float, arrow: String?, onError: (String) -> Unit) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(prompt, color = C.ink, fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, textAlign = TextAlign.Center, modifier = Modifier.height(52.dp))
        Box(Modifier.widthIn(max = 300.dp).fillMaxWidth(0.78f).aspectRatio(1f), contentAlignment = Alignment.Center) {
            Box(Modifier.fillMaxSize().padding(14.dp).clip(CircleShape).background(C.ink)) { FaceCamera(grabber, Modifier.fillMaxSize(), onError) }
            val track = C.line
            val bar = C.brand
            Canvas(Modifier.fillMaxSize()) {
                val w = 6.dp.toPx()
                val s = Size(size.width - w, size.height - w)
                drawArc(track, 0f, 360f, false, Offset(w / 2, w / 2), s, style = Stroke(w))
                drawArc(bar, -90f, 360f * progress.coerceIn(0f, 1f), false, Offset(w / 2, w / 2), s, style = Stroke(w, cap = StrokeCap.Round))
            }
            if (arrow != null) Box(
                Modifier.align(if (arrow == "LEFT") Alignment.CenterStart else Alignment.CenterEnd).offset(x = if (arrow == "LEFT") (-6).dp else 6.dp)
                    .size(48.dp).clip(CircleShape).background(C.brand), contentAlignment = Alignment.Center,
            ) { Ic(if (arrow == "LEFT") "arrowLeft" else "arrowRight", C.brandInk, 26.dp) }
        }
    }
}

/** Grabs [count] frames [intervalMs] apart; null if the camera could not keep up. */
private suspend fun captureBurst(grabber: FrameGrabber, count: Int, intervalMs: Long, onFrame: (Int) -> Unit): List<String>? {
    val frames = mutableListOf<String>()
    var ticks = 0
    while (frames.size < count && ticks < Burst.maxTicks(count)) {
        ticks++
        val started = SystemClock.elapsedRealtime()
        val jpeg = withTimeoutOrNull(1_000) { grabber.grab() }
        if (jpeg != null) {
            frames += jpegDataUrl(jpeg)
            onFrame(frames.size)
        }
        val wait = intervalMs - (SystemClock.elapsedRealtime() - started)
        if (wait > 0) delay(wait)
    }
    return frames.takeIf { it.size == count }
}

// ---------------------------------------------------------------- face setup (AMFA enrolment)

@Composable
fun FaceSetupScreen(vm: AppViewModel, s: Screen.FaceSetup) {
    val grabber = remember { FrameGrabber() }
    val scope = rememberCoroutineScope()
    var agreed by rememberSaveable { mutableStateOf(!s.consentRequired) }
    var capturing by remember { mutableStateOf(false) }
    var cue by remember { mutableStateOf(FaceSetup.CUES[0]) }
    var progress by remember { mutableFloatStateOf(0f) }
    var arrow by remember { mutableStateOf<String?>(null) }
    var cameraError by remember { mutableStateOf<String?>(null) }

    fun start() {
        if (capturing || vm.busy) return
        vm.error = null
        capturing = true
        scope.launch {
            val frames = mutableListOf<String>()
            for ((i, c) in FaceSetup.CUES.withIndex()) {
                cue = "$c · picture ${i + 1} of ${FaceSetup.CUES.size}"
                arrow = if (c.contains("left")) "LEFT" else if (c.contains("right")) "RIGHT" else null
                progress = i / FaceSetup.CUES.size.toFloat()
                // time to move after a new cue; the first picture waits a beat too
                delay(if (i == 0 || c != FaceSetup.CUES[i - 1]) FaceSetup.NEW_CUE_PAUSE_MS else FaceSetup.PICTURE_GAP_MS)
                val jpeg = withTimeoutOrNull(2_000) { grabber.grab() }
                if (jpeg == null) { vm.error = "The camera did not deliver a picture. Please try again."; capturing = false; arrow = null; progress = 0f; return@launch }
                frames += jpegDataUrl(jpeg)
            }
            progress = 1f
            arrow = null
            capturing = false
            vm.submitFaceSetup(frames)
        }
    }

    Page {
        TopBar(label = "Step 2 of 2", onBack = { vm.back() })
        StepsBar(2, 2)
        H1("Set up your face")
        Lede("Five quick pictures, about 30 seconds.")
        Messages(vm)
        if (!agreed) {
            CardBox {
                H3("Before we start")
                Muted("AMFA turns your pictures into an encrypted face template to confirm payments that look unusual and to detect impersonation. The pictures themselves are never stored. Demo Bank deletes the template if you ask.")
                Btn("I agree, continue") { agreed = true }
            }
        } else {
            cameraError?.let { Note("bad", "warn", it) }
            CameraPermissionGate {
                FaceCircle(grabber, if (capturing) cue else "Put your face inside the circle", progress, arrow) { cameraError = it }
                when {
                    vm.busy -> Busy("Checking the pictures...")
                    capturing -> Busy("Taking pictures...")
                    else -> Btn("Start") { start() }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- verify a payment (AMFA checks)

@Composable
fun VerifyScreen(vm: AppViewModel, s: Screen.Verify) {
    val ch = s.session.challenge
    Page {
        TopBar(label = "Confirm it's you", onBack = { vm.back() })
        StepsBar(s.session.steps.indexOfFirst { it == ch?.factor }.coerceAtLeast(0) + 1, s.session.steps.size.coerceAtLeast(1))
        CardBox {
            Muted("You're approving", small = true)
            H2("${s.session.amount} to ${s.session.payee}")
        }
        Messages(vm)
        when (ch?.factor) {
            "FACE" -> FaceCheck(vm, ch)
            "OTP" -> CodeCheck(vm, ch)
            "VOICE" -> VoiceCheck(vm, ch)
            "UEBA" -> TypingCheck(vm, ch)
            else -> {
                Note("warn", "info", "This payment needs a ${ch?.factor?.lowercase() ?: "different"} check, which the app doesn't support yet. Cancel and use a different payment, or finish it in the web bank.")
                Btn("Cancel payment", BtnKind.Secondary) { vm.cancelVerify() }
            }
        }
    }
}

@Composable
private fun FaceCheck(vm: AppViewModel, ch: AmfaChallenge) {
    val grabber = remember { FrameGrabber() }
    val scope = rememberCoroutineScope()
    var capturing by remember { mutableStateOf(false) }
    var captured by remember { mutableIntStateOf(0) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    val dir = if (ch.action == "TURN_LEFT") "LEFT" else "RIGHT"

    fun startBurst() {
        if (capturing || vm.busy) return
        vm.error = null
        capturing = true
        captured = 0
        scope.launch {
            val frames = captureBurst(grabber, ch.frames, ch.intervalMs) { captured = it }
            capturing = false
            if (frames != null) vm.submitFace(frames) else vm.error = "The camera did not deliver enough frames. Please try again."
        }
    }

    cameraError?.let { Note("bad", "warn", it) }
    CameraPermissionGate {
        val prompt = when {
            vm.busy -> "Checking, one moment..."
            capturing && captured * 4 < ch.frames -> "Look straight at the camera"
            capturing -> Challenge.prompt(ch.action)
            else -> "Put your face inside the circle"
        }
        FaceCircle(grabber, prompt, if (capturing || vm.busy) captured / ch.frames.toFloat() else 0f, if (capturing && captured * 4 >= ch.frames) dir else null) { cameraError = it }
        Muted(Challenge.instructions(ch.action), align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        when {
            vm.busy -> Busy("Checking liveness and matching your face...")
            capturing -> Busy("Capturing...")
            else -> Btn("Verify identity") { startBurst() }
        }
    }
}

@Composable
private fun CodeCheck(vm: AppViewModel, ch: AmfaChallenge) {
    var code by rememberSaveable { mutableStateOf("") }
    CardBox {
        H3(if (ch.mode == "totp") "Enter your authenticator code" else "Enter the code we sent")
        Muted(if (ch.mode == "totp") "Open your authenticator app and enter the 6-digit code for this bank." else "We sent it to ${ch.sentTo ?: "you"}. The message names this amount and payee, so check they match.")
        CodeBoxes(code, { code = it }, enabled = !vm.busy)
        if (ch.mode != "totp") LinkText("Send a new code", small = true, modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.resendCode() }
    }
    Btn("Confirm", enabled = code.length == 6, busy = vm.busy) { vm.submitCode(code); code = "" }
}
