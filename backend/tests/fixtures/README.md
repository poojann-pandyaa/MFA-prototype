# backend/tests/fixtures/README.md

Several tasks in docs/superpowers/plans/2026-10-01-active-liveness-challenge.md
need one real photo of a face to confirm detection/alignment/liveness work
against an actual human face, not just synthetic arrays. Public datasets
aren't committed here to keep the repo small and license-simple.

Add your own: capture a clear, front-facing, well-lit selfie (webcam or
phone is fine) and save it as `backend/tests/fixtures/sample_face.jpg`.
This file is gitignored (see Task 9) - it's a local manual-testing aid,
not a committed asset.
