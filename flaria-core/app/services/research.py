"""Research mode: participant information, consent versioning and de-identified export.

The consent text is generated from the study settings. Its version is a hash
of the exact wording, so changing any study detail (title, investigator,
ethics reference) automatically asks every participant to consent again.

De-identification in exports:
* participants appear only as study codes (FLR-0001...), never names, e-mails,
  user ids or IP addresses;
* calendar dates are replaced by day offsets from each participant's first
  session, and sample times by seconds from the session start;
* clinicians' free-text notes are never exported (they may contain names,
  places or dates);
* withdrawn participants are excluded entirely.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.config import Settings
from app.services.features_audio import AUDIO_FEATURE_NAMES
from app.services.features_vision import VISION_FEATURE_NAMES

LABELS: dict[str, str] = {
    "healthy_control": "Healthy control",
    "stroke": "Stroke",
    "als": "ALS (motor neurone disease)",
    "parkinsons": "Parkinson's disease",
    "bells_palsy": "Bell's palsy / facial nerve palsy",
    "respiratory": "Respiratory condition",
    "other": "Other confirmed condition",
}
SEVERITIES = ("mild", "moderate", "severe")


@dataclass(frozen=True, slots=True)
class StudyInfo:
    title: str
    institution: str
    investigator: str
    contact_email: str
    ethics_reference: str
    sections: list[dict[str, str]]
    agreements: list[str]
    version: str

    @property
    def approved(self) -> bool:
        return bool(self.ethics_reference.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "institution": self.institution, "investigator": self.investigator,
            "contact_email": self.contact_email, "ethics_reference": self.ethics_reference,
            "approved": self.approved, "sections": self.sections, "agreements": self.agreements,
            "version": self.version,
        }


def study_info(s: Settings) -> StudyInfo:
    who = ", ".join(x for x in (s.study_investigator.strip(), s.study_institution.strip()) if x) or "the study team"
    contact = s.study_contact_email.strip() or "the study team"
    ethics = (
        f"This study was approved by an ethics committee (reference {s.study_ethics_reference.strip()})."
        if s.study_ethics_reference.strip()
        else "This study has not yet received ethics approval. Until it does, data collected here must not be "
             "used for research or publication."
    )
    sections = [
        {"heading": "What this study is about",
         "body": f"{s.study_title}. It explores whether changes in voice and facial movement, measured by Flaria, "
                 "relate to neurological and respiratory conditions. Flaria is a research tool: it does not diagnose, "
                 "and the results you see are demonstrations, not health advice."},
        {"heading": "What taking part involves",
         "body": "You run short sessions, reading a passage aloud while facing your camera. Each takes one to three "
                 "minutes, and you can stop at any time."},
        {"heading": "What is collected",
         "body": "Video never leaves your device: your browser turns it into face measurements. Audio is analysed in "
                 "server memory and never stored. What is stored: one-second averages of 20 voice and face measures, "
                 "when each session happened, and the group a clinician confirms for you, such as \"healthy control\" "
                 "or \"stroke\"."},
        {"heading": "How it is used",
         "body": "Researchers receive these measurements under a study code such as FLR-0007, without your name, "
                 "e-mail address or real dates. They may be used to train and test software that detects change in "
                 "voice and facial movement, and in publications that never identify you."},
        {"heading": "Your choices",
         "body": "Taking part is voluntary and doesn't affect your care. You can withdraw at any time in Account "
                 "settings; your data is then left out of every future export. You can also delete your recorded "
                 "measurements there. Data exported before you withdraw can't be recalled."},
        {"heading": "Risks and benefits",
         "body": "There is no direct benefit to you. The main risk is to privacy, which the protections above keep "
                 "small. If you have symptoms that worry you, contact a doctor; don't wait for anything from this study."},
        {"heading": "Ethics approval and contact",
         "body": f"{ethics} The study is run by {who}. Questions or concerns: {contact}."},
    ]
    agreements = [
        "I have read this information and have had the chance to ask questions.",
        "I agree to take part, and to my de-identified measurements being used as described.",
    ]
    digest = hashlib.sha256(json.dumps([s.study_title, sections, agreements], sort_keys=True).encode()).hexdigest()
    return StudyInfo(s.study_title, s.study_institution, s.study_investigator, s.study_contact_email,
                     s.study_ethics_reference, sections, agreements, f"v1-{digest[:10]}")


# ----------------------------------------------------------------------------- export
SAMPLE_COLUMNS = (
    ["study_code", "session", "t_seconds"]
    + [f"audio_{n}" for n in AUDIO_FEATURE_NAMES]
    + [f"vision_{n}" for n in VISION_FEATURE_NAMES]
    + ["f0_hz", "jitter_local", "shimmer_local", "hnr_db", "anomaly", "anomaly_peak", "risk_status",
       "face_coverage", "voiced_ratio"]
)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def build_export(
    *,
    info: StudyInfo,
    participants: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    samples: Iterable[dict[str, Any]],
) -> tuple[bytes, dict[str, int]]:
    """Build the export zip. Inputs are already restricted to consenting participants."""
    first_session: dict[str, datetime] = {}
    for s in sessions:
        code = s["study_code"]
        if code not in first_session or s["started_at"] < first_session[code]:
            first_session[code] = s["started_at"]

    session_key: dict[Any, tuple[str, int, datetime]] = {}
    per_participant: dict[str, int] = {}
    session_rows = []
    for s in sorted(sessions, key=lambda r: (r["study_code"], r["started_at"])):
        code = s["study_code"]
        per_participant[code] = per_participant.get(code, 0) + 1
        idx = per_participant[code]
        session_key[s["session_id"]] = (code, idx, s["started_at"])
        duration = (s["ended_at"] - s["started_at"]).total_seconds() if s["ended_at"] else None
        a = s.get("assessment") or {}
        session_rows.append([
            code, idx, (s["started_at"] - first_session[code]).days, _fmt(duration), s["status"],
            s["tick_count"], _fmt(s["mean_anomaly"]), _fmt(s["peak_anomaly"]), s.get("triage") or "",
            "yes" if a.get("demo") else "no", s["model_version"],
        ])

    sample_buf = io.StringIO()
    w = csv.writer(sample_buf)
    w.writerow(SAMPLE_COLUMNS)
    n_samples = 0
    for r in samples:
        key = session_key.get(r["session_id"])
        if key is None:
            continue
        code, idx, started = key
        audio = list(r["audio_vec"]) if r["audio_vec"] is not None else [None] * len(AUDIO_FEATURE_NAMES)
        vision = list(r["vision_vec"]) if r["vision_vec"] is not None else [None] * len(VISION_FEATURE_NAMES)
        w.writerow([code, idx, _fmt(round((r["ts"] - started).total_seconds(), 1))]
                   + [_fmt(x) for x in audio] + [_fmt(x) for x in vision]
                   + [_fmt(r[k]) for k in ("f0_hz", "jitter_local", "shimmer_local", "hnr_db", "anomaly",
                                           "anomaly_peak", "risk_status", "face_coverage", "voiced_ratio")])
        n_samples += 1

    def table(header: list[str], rows: list[list[Any]]) -> str:
        buf = io.StringIO()
        cw = csv.writer(buf)
        cw.writerow(header)
        cw.writerows(rows)
        return buf.getvalue()

    participants_csv = table(
        ["study_code", "label", "severity", "sessions", "consent_version"],
        [[p["study_code"], p.get("label") or "unlabelled", p.get("severity") or "", per_participant.get(p["study_code"], 0),
          p["consent_version"]] for p in sorted(participants, key=lambda x: x["study_code"])],
    )
    sessions_csv = table(
        ["study_code", "session", "day", "duration_seconds", "status", "updates", "mean_anomaly", "peak_anomaly",
         "result", "demo_result", "model_version"],
        session_rows,
    )
    counts = {"participants": len(participants), "sessions": len(session_rows), "samples": n_samples,
              "labelled": sum(1 for p in participants if p.get("label"))}
    readme = f"""Flaria research export
======================
Study: {info.title}
Ethics approval: {info.ethics_reference.strip() or "NOT YET APPROVED - do not use this data for research or publication"}
Consent version: {info.version}
File created (UTC): {datetime.now(timezone.utc).date().isoformat()} (the date of this export, not of any participant activity)
Participants: {counts['participants']} ({counts['labelled']} labelled), sessions: {counts['sessions']}, one-second samples: {counts['samples']}

NOT FOR CLINICAL USE. Measurements come from consumer cameras and microphones
under uncontrolled conditions. Results marked demo_result=yes came from a model
that is not clinically validated.

De-identification
- Participants appear only as study codes. No names, e-mail addresses, account
  ids or IP addresses are included.
- "day" counts days from each participant's first session; "t_seconds" counts
  seconds from the start of that session. No calendar dates are included.
- Clinicians' free-text notes are not exported. Withdrawn participants are excluded.
- Re-identification is not permitted; small groups can still be identifiable,
  so keep this file under the study's data-protection arrangements.

Files
- participants.csv  study_code, clinician-confirmed label and severity, session count
- sessions.csv      one row per session: day, duration, update count, anomaly summary, result level
- samples.csv       one row per second of session: the 15 acoustic and 5 facial measures
                    (session averages over that second), voice quality, anomaly index, quality ratios

Measures
- audio_mfcc_0..12: mel-frequency cepstral coefficients; audio_spectral_centroid_hz; audio_zcr (zero-crossing rate)
- vision_lip_asymmetry: lip-corner offset across the facial midline, per inter-ocular distance
- vision_lip_vertical: inner-lip aperture per inter-ocular distance
- vision_ear: eye aspect ratio; vision_head_pitch_deg, vision_head_yaw_deg: head pose proxies
- f0_hz, jitter_local, shimmer_local (fractions), hnr_db: voice quality
- anomaly: model output 0..1 (empty while calibrating); risk_status: state at that second
- face_coverage, voiced_ratio: fraction of the second with a face / voiced speech
Empty cells mean the measure wasn't available in that second.
"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        z.writestr("participants.csv", participants_csv)
        z.writestr("sessions.csv", sessions_csv)
        z.writestr("samples.csv", sample_buf.getvalue())
    return out.getvalue(), counts
