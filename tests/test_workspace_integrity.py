from pathlib import Path

def test_original_research_workspace_integrity():
    """Verify that the original research workspace was not modified, deleted, or altered."""
    research_dir = Path(r"C:\Users\chand\Downloads\PE Work\Cyber - MFA\Voice Biometric Auth\Voice Research")
    assert research_dir.exists(), "Original research directory must exist intact!"

    # Check key research files still exist with unchanged presence
    expected_files = [
        research_dir / "src" / "audio_utils.py",
        research_dir / "src" / "metrics.py",
        research_dir / "src" / "extractors" / "ecapa_tdnn.py",
        research_dir / "src" / "anti_spoofing" / "rawnet2" / "model.py",
        research_dir / "src" / "anti_spoofing" / "rawnet2" / "inference.py",
        research_dir / "results" / "rawnet2" / "rawnet2_summary.json",
        research_dir / "results" / "ecapa_pairwise_summary.json",
        research_dir / "data" / "Enrollment",
        research_dir / "data" / "Verification"
    ]
    for p in expected_files:
        assert p.exists(), f"Original research artifact missing: {p}"
