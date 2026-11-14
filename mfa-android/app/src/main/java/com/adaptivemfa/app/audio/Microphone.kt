package com.adaptivemfa.app.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.adaptivemfa.app.logic.level

/** What a recording produced: 16 kHz mono 16-bit samples. */
class Recording(val samples: ShortArray, val rate: Int) {
    val seconds: Double get() = samples.size / rate.toDouble()
}

/** Records the microphone at 16 kHz (what AMFA's voice service wants) on a background thread. */
class Microphone {
    @Volatile var level = 0.0
        private set
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    @Volatile private var running = false
    private val chunks = ArrayList<ShortArray>()

    /** Needs the RECORD_AUDIO permission. Throws IllegalStateException if the microphone cannot be opened. */
    @SuppressLint("MissingPermission")
    fun start() {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) throw IllegalStateException("This device cannot record audio.")
        val r = AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, min * 2)
        if (r.state != AudioRecord.STATE_INITIALIZED) { r.release(); throw IllegalStateException("The microphone could not be opened.") }
        record = r
        chunks.clear()
        running = true
        r.startRecording()
        thread = Thread {
            val block = ShortArray(min)
            while (running) {
                val n = r.read(block, 0, block.size)
                if (n > 0) {
                    synchronized(chunks) { chunks += block.copyOf(n) }
                    level = level(block, n)
                }
            }
        }.also { it.start() }
    }

    fun stop(): Recording {
        running = false
        thread?.join(500)
        record?.let { try { it.stop() } catch (_: IllegalStateException) { }; it.release() }
        record = null
        level = 0.0
        val out = synchronized(chunks) { chunks.toList() }
        val all = ShortArray(out.sumOf { it.size })
        var o = 0
        out.forEach { System.arraycopy(it, 0, all, o, it.size); o += it.size }
        return Recording(all, RATE)
    }

    companion object { const val RATE = 16_000 }
}
