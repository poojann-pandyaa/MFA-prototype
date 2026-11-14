package com.adaptivemfa.app.ui

import android.os.SystemClock
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.BuildConfig
import com.adaptivemfa.app.camera.CameraPermissionGate
import com.adaptivemfa.app.camera.FaceCamera
import com.adaptivemfa.app.camera.FrameGrabber
import com.adaptivemfa.app.data.ActivityItem
import com.adaptivemfa.app.data.AmfaChallenge
import com.adaptivemfa.app.logic.Burst
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.FaceSetup
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.jpegDataUrl
import com.adaptivemfa.app.logic.parseRupees
import com.adaptivemfa.app.logic.registrationProblem
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

// ---------------------------------------------------------------- Sign in

@Composable
fun LoginScreen(vm: AppViewModel) {
    var user by rememberSaveable { mutableStateOf(vm.username) }
    var password by rememberSaveable { mutableStateOf("") }
    Page("Demo Bank", actionLabel = "Settings", onAction = { vm.go(Screen.Settings) }) {
        Text("Sign in", fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(
            "Everyday payments stay one tap. Anything unusual gets a quick face or code check, run by AMFA.",
            color = Color.DarkGray, fontSize = 14.sp,
        )
        Messages(vm)
        Panel {
            Field("Username", user, { user = it }, enabled = !vm.busy)
            Field("Password", password, { password = it }, password = true, enabled = !vm.busy)
            Button(
                onClick = { vm.login(user, password) },
                enabled = user.isNotBlank() && password.isNotEmpty() && !vm.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (vm.busy) "Signing in..." else "Sign in") }
        }
        TextButton(onClick = { vm.go(Screen.Register) }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Text("New here? Open an account")
        }
        Text("Bank: ${vm.store.baseUrl}", color = Color.Gray, fontSize = 12.sp, modifier = Modifier.align(Alignment.CenterHorizontally))
    }
}

// ---------------------------------------------------------------- Open an account

@Composable
fun RegisterScreen(vm: AppViewModel) {
    var user by rememberSaveable { mutableStateOf("") }
    var name by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var confirm by rememberSaveable { mutableStateOf("") }
    val problem = registrationProblem(user.trim().lowercase(), name, phone.trim(), email, password, confirm)
    Page("Open an account", backLabel = "Back", onBack = { vm.back() }) {
        Text("Create your account", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text("Next you will set up a face check, which AMFA asks for only when a payment looks unusual.", color = Color.DarkGray, fontSize = 14.sp)
        Messages(vm)
        Panel {
            Field("Full name", name, { name = it }, enabled = !vm.busy, keyboard = KeyboardType.Text)
            Field("Email (codes are sent here)", email, { email = it }, enabled = !vm.busy, keyboard = KeyboardType.Email)
            Field("Mobile number", phone, { phone = it }, enabled = !vm.busy, keyboard = KeyboardType.Phone)
            Field("Username", user, { user = it }, enabled = !vm.busy)
            Field("Password", password, { password = it }, password = true, enabled = !vm.busy)
            Field("Confirm password", confirm, { confirm = it }, password = true, enabled = !vm.busy)
            if (problem != null && (user.isNotEmpty() || name.isNotEmpty())) Text(problem, color = Bad, fontSize = 13.sp)
        }
        Button(
            onClick = { vm.register(user, name, phone, email, password) },
            enabled = problem == null && !vm.busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (vm.busy) "Creating account..." else "Open account") }
    }
}

// ---------------------------------------------------------------- Camera frame with face guide

@Composable
private fun CameraBox(grabber: FrameGrabber, caption: String, highlight: Color, onError: (String) -> Unit) {
    Box(
        Modifier.fillMaxWidth().aspectRatio(3f / 4f).clip(RoundedCornerShape(16.dp)).background(Color.Black),
        contentAlignment = Alignment.Center,
    ) {
        FaceCamera(grabber, Modifier.fillMaxSize(), onError)
        Box(
            Modifier.fillMaxWidth(0.62f).aspectRatio(0.78f).border(BorderStroke(3.dp, highlight), CircleShape),
        )
        Box(Modifier.fillMaxSize().padding(12.dp), contentAlignment = Alignment.BottomCenter) {
            Text(
                caption,
                color = Color.White,
                textAlign = TextAlign.Center,
                modifier = Modifier.background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(6.dp)).padding(horizontal = 10.dp, vertical = 6.dp),
            )
        }
    }
}

// ---------------------------------------------------------------- Taking frames

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

// ---------------------------------------------------------------- Face setup (AMFA enrolment)

@Composable
fun FaceSetupScreen(vm: AppViewModel, s: Screen.FaceSetup) {
    val grabber = remember { FrameGrabber() }
    val scope = rememberCoroutineScope()
    var agreed by rememberSaveable { mutableStateOf(!s.consentRequired) }
    var capturing by remember { mutableStateOf(false) }
    var cue by remember { mutableStateOf(FaceSetup.CUES[0]) }
    var cameraError by remember { mutableStateOf<String?>(null) }

    fun start() {
        if (capturing || vm.busy) return
        vm.error = null
        capturing = true
        scope.launch {
            val frames = mutableListOf<String>()
            for ((i, c) in FaceSetup.CUES.withIndex()) {
                cue = "$c  (picture ${i + 1} of ${FaceSetup.CUES.size})"
                // time to move after a new cue; the first picture waits a beat too
                delay(if (i == 0 || c != FaceSetup.CUES[i - 1]) FaceSetup.NEW_CUE_PAUSE_MS else FaceSetup.PICTURE_GAP_MS)
                val jpeg = withTimeoutOrNull(2_000) { grabber.grab() }
                if (jpeg == null) { vm.error = "The camera did not deliver a picture. Please try again."; capturing = false; return@launch }
                frames += jpegDataUrl(jpeg)
            }
            capturing = false
            vm.submitFaceSetup(frames)
        }
    }

    Page("Set up your face", backLabel = "Later", onBack = { vm.back() }) {
        Text("Five quick pictures, about 30 seconds.", color = Color.DarkGray, fontSize = 14.sp)
        Messages(vm)
        if (!agreed) {
            Panel {
                Text("Before we start", fontWeight = FontWeight.SemiBold)
                Text(
                    "AMFA turns your pictures into an encrypted face template to confirm payments that look unusual and to detect " +
                        "impersonation. The pictures themselves are never stored. Demo Bank deletes the template if you ask.",
                    fontSize = 14.sp,
                )
                Button(onClick = { agreed = true }, modifier = Modifier.fillMaxWidth()) { Text("I agree, continue") }
            }
        } else {
            cameraError?.let { Banner(it, Bad) }
            CameraPermissionGate {
                CameraBox(grabber, if (capturing) cue else "Put your face inside the oval", if (capturing) Good else Color.White.copy(alpha = 0.8f)) { cameraError = it }
                when {
                    vm.busy -> Busy("Checking the pictures...")
                    capturing -> Busy("Taking pictures...")
                    else -> Button(onClick = { start() }, modifier = Modifier.fillMaxWidth()) { Text("Start") }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- Home

@Composable
fun DashboardScreen(vm: AppViewModel) {
    LaunchedEffect(Unit) { if (vm.activity.isEmpty()) vm.refreshHome() }
    val me = vm.me
    var payee by rememberSaveable { mutableStateOf("") }
    var amount by rememberSaveable { mutableStateOf("") }
    val chosen = payee.ifEmpty { me?.payees?.firstOrNull()?.ref ?: "" }
    val rupees = parseRupees(amount)
    Page("Demo Bank", actionLabel = "Sign out", onAction = { vm.logout() }) {
        Text("Hello, ${me?.name?.ifBlank { null } ?: vm.username}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Messages(vm)
        Panel {
            Text("Balance", color = Color.Gray, fontSize = 13.sp)
            Text(if (me == null) "..." else formatMoney(me.balanceMinor), fontSize = 28.sp, fontWeight = FontWeight.Bold)
            if (me != null) Text("Protection: ${me.protection.ifBlank { "Basic" }}", fontSize = 13.sp, color = Color.DarkGray)
        }
        if (me != null && !me.faceEnrolled) {
            Banner("Your face check is not set up yet, so unusual payments can't be approved.", Warn)
            OutlinedButton(onClick = { vm.startFaceSetup() }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) { Text("Set up face check") }
        }
        Panel {
            Text("Send money", fontWeight = FontWeight.SemiBold)
            me?.payees?.forEach { p ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { payee = p.ref }) { Text(if (p.ref == chosen) "● ${p.display}" else "○ ${p.display}") }
                }
            }
            Field("Amount in rupees", amount, { amount = it }, enabled = !vm.busy, keyboard = KeyboardType.Decimal)
            Button(
                onClick = { rupees?.let { vm.sendMoney(chosen, it) } },
                enabled = me != null && chosen.isNotEmpty() && rupees != null && !vm.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (vm.busy) "Working..." else "Send") }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Activity", fontWeight = FontWeight.SemiBold, fontSize = 18.sp, modifier = Modifier.weight(1f))
            TextButton(onClick = { vm.refreshHome() }, enabled = !vm.busy) { Text("Refresh") }
            TextButton(onClick = { vm.go(Screen.Settings) }) { Text("Settings") }
        }
        if (!vm.busy && vm.activity.isEmpty()) Text("Nothing yet.", color = Color.Gray)
        Panel {
            vm.activity.forEachIndexed { i, item ->
                if (i > 0) HorizontalDivider()
                ActivityRow(item)
            }
        }
    }
}

@Composable
private fun ActivityRow(item: ActivityItem) {
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(item.title.ifBlank { item.payee }, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
            Text(formatMoney(item.amountMinor), fontWeight = FontWeight.SemiBold)
        }
        Text(item.status.lowercase().replace('_', ' '), fontSize = 12.sp, color = if (item.status == "DONE") Good else Color.Gray)
        item.detail?.let { Text(it, fontSize = 12.sp, color = Color.Gray) }
    }
}

// ---------------------------------------------------------------- Verify a payment (AMFA checks)

@Composable
fun VerifyScreen(vm: AppViewModel, s: Screen.Verify) {
    val ch = s.session.challenge
    Page("Confirm it's you", backLabel = "Cancel", onBack = { vm.back() }) {
        Banner("${s.session.amount} to ${s.session.payee}", BrandDark)
        Messages(vm)
        when (ch?.factor) {
            "FACE" -> FaceCheck(vm, ch)
            "OTP" -> CodeCheck(vm, ch)
            else -> Panel {
                Text("This payment needs a ${ch?.factor?.lowercase() ?: "different"} check, which the app doesn't support yet.", fontSize = 14.sp)
                Text("Cancel and use a different payment, or finish it in the web bank.", fontSize = 13.sp, color = Color.DarkGray)
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

    cameraError?.let { Banner(it, Bad) }
    CameraPermissionGate {
        val caption = when {
            vm.busy -> "Captured - verifying..."
            capturing -> "${Challenge.prompt(ch.action)} ($captured/${ch.frames})"
            else -> "Put your face inside the oval"
        }
        CameraBox(grabber, caption, if (capturing) Good else Color.White.copy(alpha = 0.8f)) { cameraError = it }
        Panel {
            Text("Your challenge: ${Challenge.prompt(ch.action)}", fontWeight = FontWeight.SemiBold)
            Text(Challenge.instructions(ch.action), fontSize = 14.sp)
            when {
                vm.busy -> Busy("Checking liveness and matching your face...")
                capturing -> Busy("Capturing...")
                else -> Button(onClick = { startBurst() }, modifier = Modifier.fillMaxWidth()) { Text("Verify identity") }
            }
        }
    }
}

@Composable
private fun CodeCheck(vm: AppViewModel, ch: AmfaChallenge) {
    var code by rememberSaveable { mutableStateOf("") }
    Panel {
        Text(if (ch.mode == "totp") "Enter your authenticator code" else "Enter the code we sent to ${ch.sentTo ?: "you"}", fontWeight = FontWeight.SemiBold)
        Field("6-digit code", code, { code = it.filter(Char::isDigit).take(6) }, enabled = !vm.busy, keyboard = KeyboardType.NumberPassword)
        Button(
            onClick = { vm.submitCode(code); code = "" },
            enabled = code.length == 6 && !vm.busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (vm.busy) "Checking..." else "Confirm") }
    }
}

// ---------------------------------------------------------------- Settings

@Composable
fun SettingsScreen(vm: AppViewModel) {
    var url by rememberSaveable { mutableStateOf(vm.store.baseUrl) }
    Page("Settings", backLabel = "Back", onBack = { vm.back() }) {
        Messages(vm)
        Panel {
            Text("Bank server address", fontWeight = FontWeight.SemiBold)
            Field("Bank URL", url, { url = it }, enabled = !vm.busy)
            Text(
                "Android emulator: http://10.0.2.2:8050 (your computer's localhost).\n" +
                    "Real phone: http://<your computer's Wi-Fi IP>:8050, same Wi-Fi. AMFA's own address comes from the bank; when it " +
                    "says 127.0.0.1 the app uses the bank's host instead (AMFA on port 8040 of the same computer).",
                fontSize = 13.sp, color = Color.DarkGray,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { if (vm.saveBaseUrl(url)) { url = vm.store.baseUrl; vm.notice = "Saved" } }, enabled = !vm.busy) { Text("Save") }
                OutlinedButton(onClick = { vm.testConnection(url) }, enabled = !vm.busy) { Text(if (vm.busy) "Testing..." else "Test connection") }
            }
        }
        Panel {
            Text("About", fontWeight = FontWeight.SemiBold)
            Text("Adaptive MFA ${BuildConfig.VERSION_NAME}", fontSize = 13.sp)
            Text("Device id: ${vm.store.deviceId}", fontSize = 13.sp, color = Color.DarkGray)
            Text("Face detection, liveness and matching all run on AMFA's servers. The app only captures camera frames.", fontSize = 13.sp, color = Color.DarkGray)
        }
    }
}
