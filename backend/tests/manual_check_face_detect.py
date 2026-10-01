"""
Manual check (needs tests/fixtures/sample_face.jpg - see fixtures/README.md).
Run: cd backend && python tests/manual_check_face_detect.py
"""
import cv2
from services import face_detect

img = cv2.imread("tests/fixtures/sample_face.jpg")
if img is None:
    raise SystemExit("Add tests/fixtures/sample_face.jpg first - see tests/fixtures/README.md")

result = face_detect.detect(img)
print("bbox:", result.bbox)
print("num landmarks:", 0 if result.landmarks_px is None else len(result.landmarks_px))
print("multi_face:", result.multi_face)
assert result.bbox is not None, "expected a face to be detected in sample_face.jpg"
assert result.landmarks_px is not None and len(result.landmarks_px) == 478
print("OK - face detected with landmarks.")
