from app.services.findings import RED_FLAGS, assess


def summary(**overrides):
    base = {
        "persisted_baseline": True,
        "seconds": 60.0,
        "valid_seconds": {"face": 55.0, "voice": 40.0},
        "z": {"audio": [0.0] * 15, "vision": [0.0] * 5, "voice": [0.0] * 4},
        "status_seconds": {"STABLE": 55.0, "WATCH": 0.0, "ELEVATED": 0.0, "CALIBRATING": 5.0, "SIGNAL_LOST": 0.0},
    }
    base.update(overrides)
    return base


def test_no_change():
    a = assess(summary())
    assert a.triage == "NO_CHANGE" and not a.findings


def test_baseline_building_never_reports_findings():
    z = {"audio": None, "vision": None, "voice": None}
    a = assess(summary(persisted_baseline=False, z=z))
    assert a.triage == "BASELINE" and not a.findings


def test_insufficient_signal():
    a = assess(summary(valid_seconds={"face": 3.0, "voice": 1.0}))
    assert a.triage == "INSUFFICIENT"


def test_voice_change_is_discuss_or_doctor():
    a = assess(summary(z={"audio": [0.0] * 15, "vision": [0.0] * 5, "voice": [0.0, 2.0, 1.0, -0.5]}))
    assert a.triage == "DISCUSS" and a.findings[0].id == "voice_quality"
    a = assess(summary(z={"audio": [0.0] * 15, "vision": [0.0] * 5, "voice": [0.0, 4.0, 3.5, -3.2]}))
    assert a.triage == "SEE_DOCTOR"


def test_strong_asymmetry_with_speech_change_escalates_to_emergency():
    z = {"audio": [0.0] * 15, "vision": [4.5, -2.0, 0.0, 0.0, 0.0], "voice": [0.0, 2.5, 0.0, 0.0]}
    a = assess(summary(z=z))
    assert a.triage == "EMERGENCY" and a.headline == "Possible stroke warning signs"
    assert {f.id for f in a.findings} >= {"facial_asymmetry", "voice_quality", "reduced_mouth_movement"}


def test_reported_warning_sign_always_wins():
    a = assess(summary(persisted_baseline=False, valid_seconds={"face": 0, "voice": 0}), ["arm_weakness", "bogus"])
    assert a.triage == "EMERGENCY" and a.red_flags == ["arm_weakness"]
    assert all(k in RED_FLAGS for k in a.red_flags)


def test_no_medication_advice_anywhere():
    z = {"audio": [0.0] * 13 + [3.0, 3.0], "vision": [4.0, -3.0, -3.0, 0, 0], "voice": [3.0, 4.0, 4.0, -4.0]}
    for flags in ([], ["chest_pain"]):
        a = assess(summary(z=z), flags)
        text = " ".join([a.headline, *a.advice] + [c for f in a.findings for c in f.possible_causes]).lower()
        for word in ("take ", "dose", "mg", "tablet", "inhaler", "antibiotic", "ibuprofen", "paracetamol"):
            assert word not in text.replace("don’t take any medicine", ""), word
