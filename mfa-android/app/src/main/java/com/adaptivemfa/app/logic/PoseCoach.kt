package com.adaptivemfa.app.logic

import kotlin.math.abs
import kotlin.math.min

/** Where the head has to be. LEFT and RIGHT are the person's own left and right, as in AMFA's TURN_LEFT and TURN_RIGHT. */
enum class Pose { STRAIGHT, LEFT, RIGHT }

/**
 * What one camera frame showed. [offset] is the nose's place between the eyes, measured exactly as AMFA's face service
 * measures it (see amfa/factors/face/gesture.py: the nose tip's offset from the eye midpoint along the eye line, divided by
 * the eye distance, on an unmirrored frame), so what turns the circle green here is what the server will accept.
 */
data class Look(val atMs: Long, val faces: Int, val offset: Double?, val faceFrac: Double, val centered: Boolean)

/**
 * The server's head-turn measure from three landmark points on an unmirrored frame: the nose's offset from the midpoint of
 * the eyes along the eye line, divided by the eye distance. The eyes are ordered by where they are in the picture (left one
 * first), whatever a detector calls them, so the sign always means what it does on the server: positive when the person turns
 * to their own left. Null if the eyes are too close together to measure.
 */
fun noseOffset(eye1x: Double, eye1y: Double, eye2x: Double, eye2y: Double, noseX: Double, noseY: Double): Double? {
    val (ax, ay, bx, by) = if (eye1x <= eye2x) listOf(eye1x, eye1y, eye2x, eye2y) else listOf(eye2x, eye2y, eye1x, eye1y)
    val dx = bx - ax
    val dy = by - ay
    val iod2 = dx * dx + dy * dy
    if (iod2 <= 1e-6) return null
    return ((noseX - (ax + bx) / 2) * dx + (noseY - (ay + by) / 2) * dy) / iod2
}

/** What to show the person right now. [ok] is the circle: red while the step is not done, green once it is. */
data class Coaching(
    val pose: Pose,
    val step: Int,
    val steps: Int,
    val ok: Boolean,
    val progress: Float,
    val message: String,
    val advanced: Boolean = false,
    val finished: Boolean = false,
)

/**
 * Walks a person through head poses and says, frame by frame, whether the current one is done. Every pose has to be held
 * for a moment before it counts, and the next one is not asked for until it has.
 *
 * [continuous] is for the payment check: the server wants the whole movement from straight to turned (in-between frames
 * included), so the frames from the start of the last straight hold to the end are kept, and the movement has to be slow
 * enough to be seen. Otherwise (face setup) one picture is kept for each pose.
 *
 * The thresholds are stricter than the server's (frontal 0.12, turn 0.18, two in-between frames), so a pose this accepts
 * passes there too.
 */
class PoseCoach(private val poses: List<Pose>, private val continuous: Boolean) {
    var step = 0
        private set
    var finished = false
        private set

    private var baseline: Double? = null
    private var holdStartMs: Long? = null
    private var holdStartIdx = 0
    private val held = ArrayList<Pair<Int, Double>>()          // frames of the current hold: (index, offset)
    private var windowStartIdx = -1                           // continuous: first frame of the straight hold the turn started from
    private val path = ArrayList<Double>()                    // continuous: offsets since that hold, to prove the movement was visible
    private var lastProgressMs = Long.MIN_VALUE
    private val pictures = ArrayList<Int>()                    // setup: the chosen frame for each finished pose
    private val seen = ArrayList<Int>()                       // frames that showed exactly one face (the server refuses any other)
    private var window: List<Int> = emptyList()

    private fun reset(look: Look, why: String): Coaching {
        step = 0; baseline = null; holdStartMs = null; held.clear(); windowStartIdx = -1; path.clear(); pictures.clear(); seen.clear(); window = emptyList()
        lastProgressMs = look.atMs
        return Coaching(poses[0], 0, poses.size, false, 0f, why)
    }

    private fun clearHold() { holdStartMs = null; held.clear() }

    fun update(index: Int, look: Look): Coaching {
        if (finished) return Coaching(poses.last(), poses.size - 1, poses.size, true, 1f, "Done", finished = true)
        if (lastProgressMs == Long.MIN_VALUE) lastProgressMs = look.atMs
        if (look.atMs - lastProgressMs > TIMEOUT_MS) return reset(look, "Let's start again. Look straight at the camera.")
        val pose = poses[step]
        fun red(msg: String, p: Float = 0f): Coaching { clearHold(); return Coaching(pose, step, poses.size, false, p, msg) }

        if (look.faces == 0 || look.offset == null) return red("We can't see your face")
        if (look.faces > 1) return red("Only you, please")
        if (look.faceFrac < FACE_MIN) return red("Move a little closer")
        if (!look.centered) return red("Put your face in the middle of the circle")

        val off = look.offset
        seen += index
        val base = baseline ?: 0.0
        val disp = off - base                      // positive: toward the person's own left
        val backFromLeft = step > 0 && poses[step - 1] == Pose.LEFT
        val backFromRight = step > 0 && poses[step - 1] == Pose.RIGHT
        val (met, progress, hint) = when (pose) {
            Pose.STRAIGHT -> Triple(abs(off) <= FRONT_OK, 0f, if (abs(off) <= FRONT_OK) "" else "Look straight at the camera")
            Pose.LEFT -> Triple(disp >= TURN_OK, (disp / TURN_OK).toFloat().coerceIn(0f, 1f),
                if (backFromRight) "Now turn your head to your left" else if (disp < -WRONG_WAY) "That's the other way: turn to your left" else if (disp >= TURN_OK) "" else "Turn your head slowly to your left")
            Pose.RIGHT -> Triple(-disp >= TURN_OK, (-disp / TURN_OK).toFloat().coerceIn(0f, 1f),
                if (backFromLeft) "Now turn your head to your right" else if (disp > WRONG_WAY) "That's the other way: turn to your right" else if (-disp >= TURN_OK) "" else "Turn your head slowly to your right")
        }
        if (continuous && step > 0 && windowStartIdx >= 0) path += disp
        if (!met) {
            clearHold()
            return Coaching(pose, step, poses.size, false, progress * 0.9f, hint)
        }
        if (holdStartMs == null) { holdStartMs = look.atMs; holdStartIdx = index }
        held += index to off
        val heldMs = look.atMs - holdStartMs!!
        val need = if (pose == Pose.STRAIGHT) STRAIGHT_HOLD_MS else TURN_HOLD_MS
        if (heldMs < need) return Coaching(pose, step, poses.size, false, 0.9f + 0.1f * (heldMs.toFloat() / need), if (pose == Pose.STRAIGHT) "Hold still" else "Hold it there")

        // the pose is done
        if (pose == Pose.STRAIGHT && baseline == null) baseline = held.map { it.second }.sorted()[held.size / 2]
        if (continuous && pose == Pose.STRAIGHT) { windowStartIdx = holdStartIdx; path.clear() }
        if (continuous && pose != Pose.STRAIGHT) {
            val final = abs(held.map { it.second - base }.sorted()[held.size / 2])
            val between = path.count { val f = abs(it) / final; f > IN_BETWEEN_LOW && f < IN_BETWEEN_HIGH }
            if (between < MIN_IN_BETWEEN) return reset(look, "Turn more slowly, so the movement can be seen")
        }
        pictures += held[held.size / 2].first
        lastProgressMs = look.atMs
        clearHold()
        if (step == poses.size - 1) {
            finished = true
            window = if (continuous) subsample(seen.filter { it >= windowStartIdx }, MAX_FRAMES) else pictures.toList()
            return Coaching(pose, step, poses.size, true, 1f, "Done", advanced = true, finished = true)
        }
        step++
        return Coaching(poses[step], step, poses.size, true, 1f, "Good", advanced = true)
    }

    /** The frames to send once [finished], as indices of the frames passed to [update], oldest first. */
    fun chosen(): List<Int> = window

    companion object {
        const val FRONT_OK = 0.08          // server accepts a frontal frame within 0.12
        const val TURN_OK = 0.24           // server needs a turn of 0.18
        const val WRONG_WAY = 0.10
        const val STRAIGHT_HOLD_MS = 500L
        const val TURN_HOLD_MS = 450L      // at least three frames of the turned pose, the window the server compares
        const val FACE_MIN = 0.22          // face width as a share of the frame
        const val IN_BETWEEN_LOW = 0.15
        const val IN_BETWEEN_HIGH = 0.85
        const val MIN_IN_BETWEEN = 3       // server needs two
        const val MAX_FRAMES = 22          // server takes 6 to 24
        const val TIMEOUT_MS = 25_000L

        /** At most [max] items, keeping the first three and the last three (the server compares those windows). */
        fun subsample(all: List<Int>, max: Int): List<Int> {
            if (all.size <= max) return all
            val keepHead = all.take(3)
            val keepTail = all.takeLast(3)
            val middle = all.subList(3, all.size - 3)
            val room = max - 6
            val picked = (0 until room).map { middle[(it * (middle.size - 1).toDouble() / min(room - 1, middle.size - 1).coerceAtLeast(1)).toInt().coerceAtMost(middle.size - 1)] }.distinct()
            return keepHead + picked + keepTail
        }
    }
}
