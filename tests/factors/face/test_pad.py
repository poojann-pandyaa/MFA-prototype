import numpy as np

from amfa.factors.face import pad


def test_a_laptop_webcam_face_stays_big_enough_for_the_pad_detector():
    """The PAD crop detector (RetinaFace) misses faces under ~90 px: on a real laptop-webcam frame it
    scored 0.0 when the frame was shrunk to 238 px wide (face ~55 px) and 0.92+ from 398 px wide. A
    landscape frame from the browser (960x540) with the face at ~22% of its width must keep it >= 120 px."""
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    small = pad.detector_input(frame)
    face_px = 0.22 * small.shape[1]
    assert face_px >= 120, small.shape


def test_small_frames_are_not_upscaled():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert pad.detector_input(frame).shape == frame.shape
