package com.adaptivemfa.app.ui

import android.app.Activity
import android.app.KeyguardManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.camera.CameraPermissionGate
import com.adaptivemfa.app.camera.FaceCamera
import com.adaptivemfa.app.camera.FrameStream
import com.adaptivemfa.app.data.AmfaChallenge
import com.adaptivemfa.app.logic.Coaching
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.Pose
import com.adaptivemfa.app.logic.PoseCoach
import com.adaptivemfa.app.logic.jpegDataUrl
import com.adaptivemfa.app.logic.registrationProblems
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

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
    val problems = registrationProblems(user.trim().lowercase(), name, digits, email, password, password)
    Page {
        TopBar(onBack = { vm.back() })
        H1("Open an account")
        Lede("It takes about a minute. Use an email you can open on this phone: your one-time codes go there.")
        Messages(vm)
        // each problem sits under its own field once the customer has tried to continue
        fun err(field: String) = if (touched) problems[field] else null
        Field("Full name", name, { name = it }, enabled = !vm.busy, error = err("name"))
        Field("Email (we send your one-time codes here)", email, { email = it }, enabled = !vm.busy, keyboard = KeyboardType.Email, placeholder = "you@example.com", error = err("email"))
        Field("Mobile number", phone, { phone = it.filter(Char::isDigit).take(10) }, enabled = !vm.busy, keyboard = KeyboardType.Phone, prefix = "+91", placeholder = "98123 45678", error = err("phone"))
        Field("Username", user, { user = it }, enabled = !vm.busy, error = err("username"))
        Field("Password", password, { password = it }, enabled = !vm.busy, password = true, error = err("password"))
        Btn("Open account", busy = vm.busy) {
            touched = true
            if (problems.isEmpty()) vm.signUp(user, name, "+91$digits", email, password)
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

// ---------------------------------------------------------------- the camera circle that reacts to the head

/** The circle: red until the step the person is on is done, green when it is; the white arc is how far along they are. */
@Composable
private fun FaceCircle(stream: FrameStream, coaching: Coaching, green: Boolean, onError: (String) -> Unit) {
    val turn = coaching.pose != Pose.STRAIGHT && !green
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Muted("Step ${minOf(coaching.step + 1, coaching.steps)} of ${coaching.steps}", small = true)
        Text(coaching.message, color = if (green) C.ok else C.ink, fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, textAlign = TextAlign.Center, modifier = Modifier.height(52.dp))
        Box(Modifier.widthIn(max = 300.dp).fillMaxWidth(0.78f).aspectRatio(1f), contentAlignment = Alignment.Center) {
            Box(Modifier.fillMaxSize().padding(14.dp).clip(CircleShape).background(C.ink)) { FaceCamera(stream, Modifier.fillMaxSize(), onError) }
            val ring = if (green) C.ok else C.bad
            val arc = Color.White
            Canvas(Modifier.fillMaxSize()) {
                val w = 7.dp.toPx()
                val s = Size(size.width - w, size.height - w)
                drawArc(ring, 0f, 360f, false, Offset(w / 2, w / 2), s, style = Stroke(w))
                if (!green && coaching.progress > 0f) drawArc(arc, -90f, 360f * coaching.progress.coerceIn(0f, 1f), false, Offset(w / 2, w / 2), s, style = Stroke(w, cap = StrokeCap.Round))
            }
            if (turn) Box(
                Modifier.align(if (coaching.pose == Pose.LEFT) Alignment.CenterStart else Alignment.CenterEnd).offset(x = if (coaching.pose == Pose.LEFT) (-6).dp else 6.dp)
                    .size(48.dp).clip(CircleShape).background(C.bad), contentAlignment = Alignment.Center,
            ) { Ic(if (coaching.pose == Pose.LEFT) "arrowLeft" else "arrowRight", Color.White, 26.dp) }
        }
    }
}

/**
 * Watches the head and walks the person through [poses]. Nothing is asked for before the last thing is done: the circle stays
 * red, with a reason, until each pose has been held; then it flashes green and asks for the next. When the last is done the
 * frames to send are handed to [onFrames].
 */
@Composable
private fun GuidedFace(poses: List<Pose>, continuous: Boolean, busy: Boolean, onFrames: (List<String>) -> Unit) {
    val stream = remember { FrameStream() }
    val coach = remember { PoseCoach(poses, continuous) }
    val scope = rememberCoroutineScope()
    var coaching by remember { mutableStateOf(Coaching(poses[0], 0, poses.size, false, 0f, "Look straight at the camera")) }
    var flash by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(stream) {
        val recent = ArrayDeque<Pair<Int, ByteArray>>()
        var i = 0
        stream.frames.collect { f ->
            if (coach.finished) return@collect
            val idx = i++
            recent.addLast(idx to f.jpeg)
            while (recent.size > 90) recent.removeFirst()
            val c = coach.update(idx, f.look)
            coaching = c
            if (c.advanced) scope.launch { flash = true; delay(450); flash = false }
            if (c.finished) {
                val byIndex = recent.toMap()
                onFrames(coach.chosen().mapNotNull { byIndex[it] }.map { jpegDataUrl(it) })
            }
        }
    }
    cameraError?.let { Note("bad", "warn", it) }
    CameraPermissionGate {
        FaceCircle(stream, coaching, flash || coach.finished || busy) { cameraError = it }
        if (busy) Busy("Checking liveness and matching your face...")
        else Muted("The circle turns green when you have done each move properly.", small = true, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    }
}

// ---------------------------------------------------------------- face setup (AMFA enrolment)

@Composable
fun FaceSetupScreen(vm: AppViewModel, s: Screen.FaceSetup) {
    var agreed by rememberSaveable { mutableStateOf(!s.consentRequired) }
    Page {
        TopBar(label = "Step 2 of 2", onBack = { vm.back() })
        StepsBar(2, 2)
        H1("Set up your face")
        Lede("Four quick pictures, each taken when your head is in the right place: straight, left, right, straight.")
        Messages(vm)
        if (!agreed) {
            CardBox {
                H3("Before we start")
                Muted("AMFA turns your pictures into an encrypted face template to confirm payments that look unusual and to detect impersonation. The pictures themselves are never stored. Demo Bank deletes the template if you ask.")
                Btn("I agree, continue") { agreed = true }
            }
        } else {
            GuidedFace(listOf(Pose.STRAIGHT, Pose.LEFT, Pose.RIGHT, Pose.STRAIGHT), continuous = false, busy = vm.busy) { vm.submitFaceSetup(it) }
        }
    }
}

// ---------------------------------------------------------------- verify a payment (AMFA checks)

@Composable
fun VerifyScreen(vm: AppViewModel, s: Screen.Verify) {
    val ch = s.session.challenge
    // a payment is "₹250 to Dairy Ram"; an account change (verify this phone, add a helper, set a limit) is described by its own title
    val act = vm.pending?.action?.takeIf { it.id == s.actionId && it.kind != "transfer" && it.kind != "bill" }
    val approving = act?.title ?: "${s.session.amount} to ${s.session.payee}"
    Page {
        TopBar(label = "Confirm it's you", onBack = { vm.back() })
        StepsBar(s.session.steps.indexOfFirst { it == ch?.factor }.coerceAtLeast(0) + 1, s.session.steps.size.coerceAtLeast(1))
        CardBox {
            Muted("You're approving", small = true)
            H2(approving)
        }
        Messages(vm)
        when (ch?.factor) {
            "FACE" -> FaceCheck(vm, ch)
            "OTP" -> CodeCheck(vm, ch, payment = act == null)
            "DEVICE" -> DeviceCheck(vm, ch, approving)
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
    val turn = if (ch.action == "TURN_LEFT") Pose.LEFT else Pose.RIGHT
    // a new challenge (after a failed try) starts a fresh coach
    key(ch.id) {
        GuidedFace(listOf(Pose.STRAIGHT, turn), continuous = true, busy = vm.busy) { vm.submitFace(it) }
    }
    Muted(Challenge.instructions(ch.action), align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
}

@Composable
private fun CodeCheck(vm: AppViewModel, ch: AmfaChallenge, payment: Boolean = true) {
    var code by rememberSaveable { mutableStateOf("") }
    CardBox {
        H3(if (ch.mode == "totp") "Enter your authenticator code" else "Enter the code we sent")
        Muted(if (ch.mode == "totp") "Open your authenticator app and enter the 6-digit code for this bank." else "We sent it to ${ch.sentTo ?: "you"}." + if (payment) " The message names this amount and payee, so check they match." else " The message names what you are approving, so check it matches.")
        val keyboard = LocalSoftwareKeyboardController.current
        CodeBoxes(code, { code = it; if (it.length == 6) keyboard?.hide() }, enabled = !vm.busy)   // the keyboard would cover Confirm
        if (ch.mode != "totp") LinkText("Send a new code", small = true, modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.resendCode() }
        if (ch.mode != "totp" && vm.me?.demoTools == true) {
            vm.demoCode?.let { Note("brand", "info", "This is a demo server, so no email is sent. Your code is $it.") }
            LinkText("Show demo code", small = true, modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.fetchDemoCode() }
        }
    }
    Btn("Confirm", enabled = code.length == 6, busy = vm.busy) { vm.submitCode(code); code = "" }
}


/** The phone's own fingerprint or screen lock: the key in the secure hardware signs once it has been confirmed. */
@Composable
private fun DeviceCheck(vm: AppViewModel, ch: AmfaChallenge, what: String) {
    val ctx = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) vm.deviceUnlocked() else vm.deviceUnlockCancelled()
    }
    LaunchedEffect(ch.id) { vm.signDevice() }
    // keyed on a counter, not the flag: asking again while the flag is still true (a prompt that never reported back) must open it again
    LaunchedEffect(vm.deviceUnlockAsks) {
        if (vm.deviceUnlock) {
            val intent = ctx.getSystemService(KeyguardManager::class.java)?.createConfirmDeviceCredentialIntent("Confirm it's you", what)
            if (intent == null) vm.deviceUnlockCancelled() else launcher.launch(intent)
        }
    }
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Tile("lock")
            Column(Modifier.weight(1f)) {
                H3("Use your fingerprint or screen lock")
                Muted("Your phone checks it's you, then confirms this. Nothing is sent from your fingerprint.", small = true)
            }
        }
    }
    Btn("Confirm", busy = vm.busy) { vm.signDevice() }
}
