package com.adaptivemfa.app.camera

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.SystemClock
import android.util.Log
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import com.google.mlkit.vision.face.FaceLandmark
import com.adaptivemfa.app.logic.Look
import com.adaptivemfa.app.logic.noseOffset
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import kotlin.math.max

/**
 * Hands out one JPEG frame on request. Frames are rotated upright and NOT mirrored, like the web
 * client's captured frames: the backend's head-turn direction check assumes unmirrored images.
 * (Only the on-screen preview is mirrored, so turning left moves your face left on screen.)
 */
/** One camera frame: the picture (upright, not mirrored, what the server gets) and what the on-device face finder saw in it. */
class Frame(val jpeg: ByteArray, val look: Look)

/**
 * Turns the camera's frames into [Frame]s about ten times a second: a JPEG for AMFA and a measurement of the head for the
 * coach. The finder is Google's on-device ML Kit face detector (no picture leaves the phone for this); AMFA's own service
 * still makes the real decision on the pictures that are sent.
 */
class FrameStream : ImageAnalysis.Analyzer {
    val frames = MutableSharedFlow<Frame>(extraBufferCapacity = 4, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /** How many frames the camera has delivered at all; zero after a few seconds means it is not working. */
    @Volatile var framesSeen = 0
        private set

    private var lastMs = 0L
    private val detector by lazy {
        FaceDetection.getClient(
            FaceDetectorOptions.Builder().setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
                .setLandmarkMode(FaceDetectorOptions.LANDMARK_MODE_ALL).setMinFaceSize(0.15f).build(),
        )
    }

    override fun analyze(image: ImageProxy) {
        framesSeen++
        try {
            val now = SystemClock.elapsedRealtime()
            if (now - lastMs < MIN_GAP_MS) return
            lastMs = now
            val bitmap = upright(image)
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            frames.tryEmit(Frame(out.toByteArray(), look(bitmap, now)))
            bitmap.recycle()
        } catch (e: Throwable) {
            Log.w("FrameStream", "frame skipped: ${e.message}")
        } finally {
            image.close()
        }
    }

    private fun upright(image: ImageProxy): Bitmap {
        val raw = image.toBitmap()
        val rotation = image.imageInfo.rotationDegrees
        val longSide = max(raw.width, raw.height)
        val scale = if (longSide > MAX_SIDE) MAX_SIDE.toFloat() / longSide else 1f
        val matrix = Matrix().apply {
            if (scale != 1f) postScale(scale, scale)
            if (rotation != 0) postRotate(rotation.toFloat())
        }
        val upright = if (rotation == 0 && scale == 1f) raw else Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
        if (upright !== raw) raw.recycle()
        return upright
    }

    private fun look(bitmap: Bitmap, now: Long): Look {
        val faces = Tasks.await(detector.process(InputImage.fromBitmap(bitmap, 0)), 2, TimeUnit.SECONDS)
        if (faces.size != 1) return Look(now, faces.size, null, 0.0, false)
        val f = faces[0]
        val eyes = listOfNotNull(f.getLandmark(FaceLandmark.LEFT_EYE)?.position, f.getLandmark(FaceLandmark.RIGHT_EYE)?.position)
        val nose = f.getLandmark(FaceLandmark.NOSE_BASE)?.position
        val offset = if (eyes.size == 2 && nose != null) noseOffset(eyes[0].x.toDouble(), eyes[0].y.toDouble(), eyes[1].x.toDouble(), eyes[1].y.toDouble(), nose.x.toDouble(), nose.y.toDouble()) else null
        val box = f.boundingBox
        val centered = abs(box.centerX() / bitmap.width.toDouble() - 0.5) <= 0.2 && abs(box.centerY() / bitmap.height.toDouble() - 0.5) <= 0.25
        return Look(now, 1, offset, box.width() / bitmap.width.toDouble(), centered)
    }

    companion object {
        /** Same size class as the web client's 720x540 frames; well under the server's 1 MB per-frame limit. */
        const val MAX_SIDE = 720
        const val JPEG_QUALITY = 85
        const val MIN_GAP_MS = 90L
    }
}

/** Front-camera preview bound to the screen's lifecycle; feeds [grabber]. */
@Composable
fun FaceCamera(grabber: FrameStream, modifier: Modifier = Modifier, onError: (String) -> Unit = {}) {
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
