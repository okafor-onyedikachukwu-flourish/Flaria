import csv
import io
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.services.research import SAMPLE_COLUMNS, build_export, study_info


def test_consent_version_tracks_the_wording():
    a = study_info(Settings(research_mode=True, study_ethics_reference=""))
    b = study_info(Settings(research_mode=True, study_ethics_reference="HREC/2026/123"))
    assert a.version != b.version, "changing study details must require fresh consent"
    assert a.version == study_info(Settings(research_mode=True, study_ethics_reference="")).version
    assert not a.approved and b.approved
    assert "not yet received ethics approval" in a.sections[-1]["body"]
    assert "HREC/2026/123" in b.sections[-1]["body"]


def test_export_is_de_identified():
    start = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    sid1, sid2, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sessions = [
        {"session_id": sid1, "study_code": "FLR-0001", "started_at": start, "ended_at": start + timedelta(seconds=90),
         "status": "COMPLETED", "tick_count": 900, "mean_anomaly": 0.2, "peak_anomaly": 0.4, "model_version": "m1",
         "triage": "NO_CHANGE", "assessment": {"demo": True}},
        {"session_id": sid2, "study_code": "FLR-0001", "started_at": start + timedelta(days=3, hours=2),
         "ended_at": start + timedelta(days=3, hours=2, seconds=60), "status": "COMPLETED", "tick_count": 600,
         "mean_anomaly": None, "peak_anomaly": None, "model_version": "m1", "triage": None, "assessment": None},
    ]
    samples = [
        {"session_id": sid1, "ts": start + timedelta(seconds=s), "audio_vec": [0.1] * 15, "vision_vec": [0.2] * 5,
         "f0_hz": 130.0, "jitter_local": 0.005, "shimmer_local": 0.04, "hnr_db": 18.0, "anomaly": 0.1,
         "anomaly_peak": 0.2, "risk_status": "STABLE", "face_coverage": 1.0, "voiced_ratio": 0.8}
        for s in range(3)
    ] + [{"session_id": other, "ts": start, "audio_vec": None, "vision_vec": None, "f0_hz": None, "jitter_local": None,
          "shimmer_local": None, "hnr_db": None, "anomaly": None, "anomaly_peak": None, "risk_status": "STABLE",
          "face_coverage": 0.0, "voiced_ratio": 0.0}]  # a session outside the export must be dropped
    participants = [{"study_code": "FLR-0001", "consent_version": "v1-x", "label": "stroke", "severity": "mild"}]
    data, counts = build_export(info=study_info(Settings(research_mode=True)), participants=participants,
                                sessions=sessions, samples=samples)
    assert counts == {"participants": 1, "sessions": 2, "samples": 3, "labelled": 1}
    z = zipfile.ZipFile(io.BytesIO(data))
    assert set(z.namelist()) == {"README.txt", "participants.csv", "sessions.csv", "samples.csv"}
    everything = "".join(z.read(n).decode() for n in z.namelist())
    for leak in (str(sid1), str(sid2), str(other), "2026-10-01", "2026-10-04", "09:30"):
        assert leak not in everything, f"export leaks {leak}"
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", everything), "export contains an e-mail address"
    sess = list(csv.DictReader(io.StringIO(z.read("sessions.csv").decode())))
    assert [r["day"] for r in sess] == ["0", "3"] and [r["session"] for r in sess] == ["1", "2"]
    assert sess[0]["demo_result"] == "yes"
    rows = list(csv.reader(io.StringIO(z.read("samples.csv").decode())))
    assert rows[0] == SAMPLE_COLUMNS and len(rows) == 4
    assert rows[1][:3] == ["FLR-0001", "1", "0"] and rows[3][2] == "2"
    assert "NOT YET APPROVED" in z.read("README.txt").decode()
