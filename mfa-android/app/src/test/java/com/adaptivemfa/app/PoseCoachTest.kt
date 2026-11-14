package com.adaptivemfa.app

import com.adaptivemfa.app.logic.Coaching
import com.adaptivemfa.app.logic.Look
import com.adaptivemfa.app.logic.Pose
import com.adaptivemfa.app.logic.PoseCoach
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Feeds a coach one frame every 100 ms, like the camera analysis does. */
private class Run(val coach: PoseCoach) {
    var at = 0L
    var idx = 0
    val log = ArrayList<Coaching>()
    fun frame(offset: Double?, faces: Int = 1, frac: Double = 0.4, centered: Boolean = true): Coaching {
        at += 100
        return coach.update(idx++, Look(at, faces, offset, frac, centered)).also { log += it }
    }
    fun hold(offset: Double, ms: Int) = repeat(ms / 100) { frame(offset) }
    /** A smooth move from [from] to [to] over [ms]. */
    fun move(from: Double, to: Double, ms: Int) { val n = ms / 100; for (i in 1..n) frame(from + (to - from) * i / n) }
}

class PoseCoachTest {
    private fun check(turn: Pose = Pose.LEFT) = Run(PoseCoach(listOf(Pose.STRAIGHT, turn), continuous = true))

    @Test fun theCircleStaysRedUntilTheFaceIsStraightAndHeldThenGoesOnToTheTurn() {
        val r = check()
        r.frame(0.30)                                           // looking away to begin with
        assertFalse(r.log.last().ok); assertEquals("Look straight at the camera", r.log.last().message)
        r.hold(0.02, 300)
        assertFalse(r.log.last().ok); assertEquals("Hold still", r.log.last().message)          // straight but not yet held long enough
        r.hold(0.02, 300)
        val advanced = r.log.first { it.advanced }
        assertTrue(advanced.ok); assertEquals(1, advanced.step)                                 // now, and only now, the next step
        assertTrue(r.log.takeWhile { !it.advanced }.none { it.ok })                             // never green before that
    }

    @Test fun aSlowTurnToTheLeftIsAcceptedAndOnlyThenFinished() {
        val r = check(Pose.LEFT)
        r.hold(0.02, 700)
        r.move(0.02, 0.32, 1200)
        assertFalse(r.coach.finished)                                                           // reaching the turn is not enough: it has to be held
        r.hold(0.32, 600)
        assertTrue(r.coach.finished)
        assertTrue(r.log.last().finished && r.log.last().ok && r.log.last().progress == 1f)
    }

    @Test fun theTurnShowsProgressAsTheHeadMoves() {
        val r = check(Pose.LEFT)
        r.hold(0.0, 700)
        val progress = (1..6).map { r.frame(it * 0.03).progress }
        assertTrue(progress.zipWithNext().all { (a, b) -> b >= a })                             // the ring follows the head, not a clock
        assertTrue(progress.last() > progress.first() && progress.last() < 1f)
        assertFalse(r.log.last().ok)
    }

    @Test fun turningTheWrongWayStaysRedAndSaysSo() {
        val r = check(Pose.LEFT)
        r.hold(0.0, 700)
        r.move(0.0, -0.30, 1000)
        r.hold(-0.30, 800)
        assertFalse(r.coach.finished); assertFalse(r.log.last().ok)
        assertTrue(r.log.last().message.startsWith("That's the other way"))
    }

    @Test fun theRightTurnNeedsTheOppositeSign() {
        val r = check(Pose.RIGHT)
        r.hold(0.0, 700); r.move(0.0, 0.30, 1000); r.hold(0.30, 800)
        assertFalse(r.coach.finished)                                                           // a left turn does not pass a right challenge
        val ok = check(Pose.RIGHT)
        ok.hold(0.0, 700); ok.move(0.0, -0.30, 1000); ok.hold(-0.30, 700)
        assertTrue(ok.coach.finished)
    }

    @Test fun aTurnThatIsTooFastToSeeIsSentBackToTheStart() {
        val r = check(Pose.LEFT)
        r.hold(0.0, 700)
        r.frame(0.30)                                                                           // snapped across in one frame: a photo swap looks like this
        r.hold(0.30, 700)
        assertFalse(r.coach.finished)
        assertTrue(r.log.any { it.message.startsWith("Turn more slowly") })
        assertEquals(0, r.coach.step)
    }

    @Test fun aTurnThatIsNotBigEnoughIsNotAccepted() {
        val r = check(Pose.LEFT)
        r.hold(0.0, 700); r.move(0.0, 0.18, 1000); r.hold(0.18, 1000)       // the server's own limit, which has no margin
        assertFalse(r.coach.finished)
    }

    @Test fun losingTheHoldStartsItAgain() {
        val r = check(Pose.LEFT)
        r.hold(0.0, 700); r.move(0.0, 0.25, 1000); r.hold(0.25, 200)       // into the turn and held for a moment...
        assertFalse(r.coach.finished)
        r.frame(0.10)                                                       // ...then drifted back: the hold is lost
        r.hold(0.30, 300)
        assertFalse(r.coach.finished)                                       // and it counts from zero again, not from before
        r.hold(0.30, 300)
        assertTrue(r.coach.finished)
    }

    @Test fun noFaceManyFacesOrASmallOrOffCentreFaceAreEachExplained() {
        val r = check()
        assertEquals("We can't see your face", r.frame(null, faces = 0).message)
        assertEquals("Only you, please", r.frame(0.0, faces = 2).message)
        assertEquals("Move a little closer", r.frame(0.0, frac = 0.1).message)
        assertEquals("Put your face in the middle of the circle", r.frame(0.0, centered = false).message)
        assertTrue(r.log.none { it.ok })
    }

    @Test fun theFramesSentRunFromTheStraightHoldToTheEndAndEachShowedOneFace() {
        val r = check(Pose.LEFT)
        r.hold(0.02, 800); r.move(0.02, 0.32, 1000)
        r.frame(null, faces = 0)                                                                // a frame with no face must not be sent
        r.hold(0.32, 600)
        val sent = r.coach.chosen()
        assertTrue(sent.size in 6..PoseCoach.MAX_FRAMES)
        assertEquals(sent.sorted(), sent)
        val noFace = r.log.indexOfFirst { it.message == "We can't see your face" }
        assertFalse(sent.contains(noFace))
        assertTrue(sent.take(3).all { it < 8 })                                                  // the first frames are from the straight hold
    }

    @Test fun aLongMovementIsThinnedToWhatTheServerAcceptsKeepingBothEnds() {
        val all = (0 until 80).toList()
        val sub = PoseCoach.subsample(all, 22)
        assertTrue(sub.size <= 22)
        assertEquals(listOf(0, 1, 2), sub.take(3)); assertEquals(listOf(77, 78, 79), sub.takeLast(3))
        assertEquals(sub.sorted(), sub)
        assertEquals(all.take(10), PoseCoach.subsample(all.take(10), 22))
    }

    @Test fun faceSetupTakesOnePictureForEachPoseAndOnlyMovesOnWhenItIsRight() {
        val r = Run(PoseCoach(listOf(Pose.STRAIGHT, Pose.LEFT, Pose.RIGHT, Pose.STRAIGHT), continuous = false))
        r.hold(0.0, 700)
        assertEquals(1, r.coach.step)
        r.move(0.0, 0.3, 800); r.hold(0.3, 600)
        assertEquals(2, r.coach.step)
        assertEquals("Now turn your head to your right", r.frame(0.3).message)                  // still left: it asks for the next pose, not "wrong way"
        r.move(0.3, -0.3, 1400); r.hold(-0.3, 600)
        assertEquals(3, r.coach.step)
        r.move(-0.3, 0.0, 800); r.hold(0.0, 700)
        assertTrue(r.coach.finished)
        assertEquals(4, r.coach.chosen().size)
        assertEquals(r.coach.chosen().sorted(), r.coach.chosen())
    }

    @Test fun anAbandonedAttemptStartsOverAfterAWhile() {
        val r = check()
        r.hold(0.02, 700)
        repeat(260) { r.frame(0.0, faces = 0) }
        assertEquals(0, r.coach.step)
        assertTrue(r.log.any { it.message.startsWith("Let's start again") })
    }
}
