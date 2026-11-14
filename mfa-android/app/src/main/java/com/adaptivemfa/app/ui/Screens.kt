package com.adaptivemfa.app.ui

import android.graphics.BitmapFactory
import android.os.SystemClock
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.BuildConfig
import com.adaptivemfa.app.camera.CameraPermissionGate
import com.adaptivemfa.app.camera.FaceCamera
import com.adaptivemfa.app.camera.FrameGrabber
import com.adaptivemfa.app.data.HistoryItem
import com.adaptivemfa.app.logic.Burst
import com.adaptivemfa.app.logic.Challenge
import com.adaptivemfa.app.logic.canSubmit
import com.adaptivemfa.app.logic.formatTimestamp
import com.adaptivemfa.app.logic.jpegDataUrl
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

// ---------------------------------------------------------------- Login (step 1)

@Composable
fun LoginScreen(vm: AppViewModel) {
    var user by rememberSaveable { mutableStateOf(vm.username) }
    var password by rememberSaveable { mutableStateOf("") }
    Page("Adaptive MFA", actionLabel = "Settings", onAction = { vm.go(Screen.Settings) }) {
        Text("Sign in", fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(
            "From a phone you have used before you only need your password. From a new phone, or at an unusual time, you will also be asked for a quick face check.",
            color = Color.DarkGray, fontSize = 14.sp,
        )
        Messages(vm)
        Panel {
            Field("Username", user, { user = it }, enabled = !vm.busy)
            Field("Password", password, { password = it }, password = true, enabled = !vm.busy)
            Button(
                onClick = { vm.login(user, password) },
                enabled = canSubmit(user, password) && !vm.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (vm.busy) "Checking..." else "Log in") }
        }
        TextButton(onClick = { vm.go(Screen.Enroll) }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Text("No account yet? Enroll")
        }
        Text("Server: ${vm.store.baseUrl}", color = Color.Gray, fontSize = 12.sp, modifier = Modifier.align(Alignment.CenterHorizontally))
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

// ---------------------------------------------------------------- Face check (step 2)

@Composable
fun FaceCheckScreen(vm: AppViewModel, s: Screen.FaceCheck) {
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
            val frames = mutableListOf<String>()
            var ticks = 0
            while (frames.size < Burst.FRAME_COUNT && ticks < Burst.MAX_TICKS) {
                ticks++
                val started = SystemClock.elapsedRealtime()
                val jpeg = withTimeoutOrNull(1_000) { grabber.grab() }
                if (jpeg != null) {
                    frames += jpegDataUrl(jpeg)
                    captured = frames.size
                }
                val wait = Burst.INTERVAL_MS - (SystemClock.elapsedRealtime() - started)
                if (wait > 0) delay(wait)
            }
            capturing = false
            if (frames.size == Burst.FRAME_COUNT) vm.submitFaceCheck(frames)
            else vm.error = "The camera did not deliver enough frames. Please try again."
        }
    }

    Page("Face check", backLabel = "Cancel", onBack = { vm.back() }) {
        Banner("${s.riskScore} risk: new device or unusual time. Please confirm it's you, ${s.username}.", riskColor(s.riskScore))
        Messages(vm)
        cameraError?.let { Banner(it, Bad) }
        CameraPermissionGate {
            val caption = when {
                vm.busy -> "Captured - verifying..."
                capturing -> "${Challenge.prompt(s.challengeType)} ($captured/${Burst.FRAME_COUNT})"
                else -> "Put your face inside the oval"
            }
            CameraBox(grabber, caption, if (capturing) Good else Color.White.copy(alpha = 0.8f)) { cameraError = it }
            Panel {
                Text("Your challenge: ${Challenge.prompt(s.challengeType)}", fontWeight = FontWeight.SemiBold)
                Text(Challenge.instructions(s.challengeType), fontSize = 14.sp)
                Text("This challenge expires about 90 seconds after you logged in.", color = Color.Gray, fontSize = 12.sp)
                when {
                    vm.busy -> Busy("Checking liveness and matching your face...")
                    capturing -> Busy("Capturing...")
                    else -> Button(onClick = { startBurst() }, modifier = Modifier.fillMaxWidth()) { Text("Verify identity") }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- Enroll

@Composable
fun EnrollScreen(vm: AppViewModel) {
    val grabber = remember { FrameGrabber() }
    val scope = rememberCoroutineScope()
    var user by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var confirm by rememberSaveable { mutableStateOf("") }
    var photo by remember { mutableStateOf<ByteArray?>(null) }
    var taking by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    val preview = remember(photo) { photo?.let { BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() } }
    val mismatch = confirm.isNotEmpty() && confirm != password

    Page("Enroll", backLabel = "Back", onBack = { vm.back() }) {
        Text("Create your account", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text(
            "Your face photo is turned into a face template on the server and compared at later logins. The photo itself is not kept on the phone.",
            color = Color.DarkGray, fontSize = 14.sp,
        )
        Messages(vm)
        Panel {
            Field("Username", user, { user = it }, enabled = !vm.busy)
            Field("Password", password, { password = it }, password = true, enabled = !vm.busy)
            Field("Confirm password", confirm, { confirm = it }, password = true, enabled = !vm.busy)
            if (mismatch) Text("Passwords do not match", color = Bad, fontSize = 13.sp)
        }
        cameraError?.let { Banner(it, Bad) }
        CameraPermissionGate {
            if (preview != null) {
                Image(
                    preview, contentDescription = "Your face photo",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxWidth().aspectRatio(3f / 4f).clip(RoundedCornerShape(16.dp)),
                )
                OutlinedButton(onClick = { photo = null }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) { Text("Retake photo") }
            } else {
                CameraBox(grabber, "Look straight at the camera, good light, no glasses glare", Color.White.copy(alpha = 0.8f)) { cameraError = it }
                Button(
                    onClick = {
                        taking = true
                        scope.launch {
                            photo = withTimeoutOrNull(3_000) { grabber.grab() }
                            if (photo == null) vm.error = "The camera did not deliver a photo. Please try again."
                            taking = false
                        }
                    },
                    enabled = !taking && !vm.busy,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (taking) "Taking photo..." else "Take photo") }
            }
        }
        Button(
            onClick = { photo?.let { vm.enroll(user, password, jpegDataUrl(it)) } },
            enabled = canSubmit(user, password) && !mismatch && confirm.isNotEmpty() && photo != null && !vm.busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (vm.busy) "Creating account..." else "Create account") }
    }
}

// ---------------------------------------------------------------- Dashboard

@Composable
fun DashboardScreen(vm: AppViewModel) {
    LaunchedEffect(Unit) { if (vm.history.isEmpty()) vm.refreshHistory() }
    Page("Dashboard", actionLabel = "Log out", onAction = { vm.logout() }) {
        Text("Welcome, ${vm.username}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Messages(vm)
        Panel {
            Text("This phone", fontWeight = FontWeight.SemiBold)
            Text("Device id: ${vm.store.deviceId}", fontSize = 13.sp, color = Color.DarkGray)
            Text("Server: ${vm.store.baseUrl}", fontSize = 13.sp, color = Color.DarkGray)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Login history", fontWeight = FontWeight.SemiBold, fontSize = 18.sp, modifier = Modifier.weight(1f))
            TextButton(onClick = { vm.refreshHistory() }, enabled = !vm.busy) { Text("Refresh") }
            TextButton(onClick = { vm.go(Screen.Settings) }) { Text("Settings") }
        }
        if (vm.busy && vm.history.isEmpty()) Busy("Loading...")
        if (!vm.busy && vm.history.isEmpty()) Text("No logins recorded yet.", color = Color.Gray)
        Panel {
            vm.history.forEachIndexed { i, item ->
                if (i > 0) HorizontalDivider()
                HistoryRow(item, thisDevice = item.deviceIdentifier == vm.store.deviceId)
            }
        }
    }
}

@Composable
private fun HistoryRow(item: HistoryItem, thisDevice: Boolean) {
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(formatTimestamp(item.timestamp), fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
            Text(if (item.success) "Success" else "Failed", color = if (item.success) Good else Bad, fontWeight = FontWeight.SemiBold)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("${item.riskScore} risk", color = riskColor(item.riskScore), fontSize = 13.sp)
            Text(if (item.faceCheckTriggered) "Face check" else "Password only", fontSize = 13.sp, color = Color.DarkGray)
        }
        Text(
            item.deviceIdentifier + if (thisDevice) "  (this phone)" else "",
            fontSize = 12.sp, color = Color.Gray,
        )
    }
}

// ---------------------------------------------------------------- Settings

@Composable
fun SettingsScreen(vm: AppViewModel) {
    var url by rememberSaveable { mutableStateOf(vm.store.baseUrl) }
    Page("Settings", backLabel = "Back", onBack = { vm.back() }) {
        Messages(vm)
        Panel {
            Text("MFA server address", fontWeight = FontWeight.SemiBold)
            Field("Server URL", url, { url = it }, enabled = !vm.busy)
            Text(
                "Android emulator: http://10.0.2.2:8000 (your computer's localhost).\n" +
                    "Real phone: http://<your computer's Wi-Fi IP>:8000, same Wi-Fi, backend started with --host 0.0.0.0.",
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
            Text("Face detection, anti-spoofing and face matching all run on the server. The app only captures camera frames.", fontSize = 13.sp, color = Color.DarkGray)
        }
    }
}
