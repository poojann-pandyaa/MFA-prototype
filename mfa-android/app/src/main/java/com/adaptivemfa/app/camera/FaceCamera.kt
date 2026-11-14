package com.adaptivemfa.app.camera

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Size
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import com.adaptivemfa.app.ui.Btn
import com.adaptivemfa.app.ui.Note
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.max

/**
 * Hands out one JPEG frame on request. Frames are rotated upright and NOT mirrored, like the web
 * client's captured frames: the backend's head-turn direction check assumes unmirrored images.
 * (Only the on-screen preview is mirrored, so turning left moves your face left on screen.)
 */
class FrameGrabber : ImageAnalysis.Analyzer {
    private val pending = AtomicReference<CompletableDeferred<ByteArray>?>(null)

    /** How many frames the camera has delivered at all; zero after a few seconds means it is not working. */
    @Volatile var framesSeen = 0
        private set

    override fun analyze(image: ImageProxy) {
        framesSeen++
        try {
            val request = pending.getAndSet(null) ?: return
            try {
                request.complete(encode(image))
            } catch (e: Throwable) {
                request.completeExceptionally(e)
            }
        } finally {
            image.close()
        }
    }

    /** Suspends until the camera delivers its next frame. */
    suspend fun grab(): ByteArray {
        val request = CompletableDeferred<ByteArray>()
        pending.set(request)
        return request.await()
    }

    private fun encode(image: ImageProxy): ByteArray {
        val raw = image.toBitmap()
        val rotation = image.imageInfo.rotationDegrees
        val longSide = max(raw.width, raw.height)
        val scale = if (longSide > MAX_SIDE) MAX_SIDE.toFloat() / longSide else 1f
        val matrix = Matrix().apply {
            if (scale != 1f) postScale(scale, scale)
            if (rotation != 0) postRotate(rotation.toFloat())
        }
        val upright = if (rotation == 0 && scale == 1f) raw else Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
        val out = ByteArrayOutputStream()
        upright.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (upright !== raw) upright.recycle()
        raw.recycle()
        return out.toByteArray()
    }

    companion object {
        /** Same size class as the web client's 720x540 frames; well under the server's 1 MB per-frame limit. */
        const val MAX_SIDE = 720
        const val JPEG_QUALITY = 85
    }
}

/** Front-camera preview bound to the screen's lifecycle; feeds [grabber]. */
@Composable
fun FaceCamera(grabber: FrameGrabber, modifier: Modifier = Modifier, onError: (String) -> Unit = {}) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val previewView = remember {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }

    LaunchedEffect(grabber) {
        delay(4_000)
        if (grabber.framesSeen == 0) onError(
            "The camera is not delivering pictures. On the emulator, allow it to use your Mac's webcam " +
                "(System Settings, Privacy & Security, Camera) and restart it; on a phone, close other apps that use the camera.",
        )
    }

    DisposableEffect(lifecycleOwner) {
        val executor = Executors.newSingleThreadExecutor()
        val future = ProcessCameraProvider.getInstance(context)
        var provider: ProcessCameraProvider? = null
        var disposed = false
        future.addListener({
            if (disposed) return@addListener
            val p = try { future.get() } catch (e: Exception) { onError("Camera unavailable: ${e.message}"); return@addListener }
            provider = p
            val selector = ResolutionSelector.Builder()
                .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                .setResolutionStrategy(ResolutionStrategy(Size(640, 480), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                .build()
            val preview = Preview.Builder().setResolutionSelector(selector).build()
            preview.setSurfaceProvider(previewView.surfaceProvider)
            val analysis = ImageAnalysis.Builder()
                .setResolutionSelector(selector)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
            analysis.setAnalyzer(executor, grabber)
            val camera = when {
                p.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) -> CameraSelector.DEFAULT_FRONT_CAMERA
                p.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) -> CameraSelector.DEFAULT_BACK_CAMERA
                else -> null
            }
            if (camera == null) {
                onError("This device has no camera.")
                return@addListener
            }
            try {
                p.unbindAll()
                p.bindToLifecycle(lifecycleOwner, camera, preview, analysis)
            } catch (e: Exception) {
                onError("Could not start the camera: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(context))

        onDispose {
            disposed = true
            provider?.unbindAll()
            executor.shutdown()
        }
    }

    AndroidView(factory = { previewView }, modifier = modifier)
}

/** Shows [content] once the camera permission is granted, otherwise a button that asks for it. */
@Composable
fun CameraPermissionGate(content: @Composable () -> Unit) {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    var denied by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        granted = ok
        denied = !ok
    }
    if (granted) {
        content()
    } else {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Note(
                "brand", "face",
                if (denied) "Camera access was refused. The face check needs the camera; allow it here or in the phone's app settings."
                else "The face check needs the front camera. Pictures go only to AMFA's servers and are not saved on the phone.",
            )
            Btn("Allow camera") { launcher.launch(Manifest.permission.CAMERA) }
        }
    }
}
