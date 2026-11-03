"""Session result: what changed, what it can mean, and what to do next.

Design constraints (deliberate, do not relax without clinical sign-off):

* Findings are *changes from this person's own baseline*, expressed as mean
  z-scores over the session. Without a persisted baseline there is nothing to
  compare with, so the result says so instead of guessing.
* "Possible causes" are the common explanations clinicians consider for each
  pattern. They are listed as possibilities, never as a diagnosis: voice and
  facial measurements cannot distinguish, for example, a stroke from Bell's
  palsy, or a chest infection from asthma.
* The engine never recommends medicines. Medication decisions need a
  clinician who knows the person's history, allergies and current medicines;
  the app shows the care team's own notes instead.
* Triage errs toward escalation. Any self-reported warning sign, or a strong
  facial asymmetry together with a speech change, produces emergency advice.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

RULES_VERSION = "2026.11-1"

TRIAGE_ORDER = ("EMERGENCY", "SEE_DOCTOR", "DISCUSS", "NO_CHANGE", "BASELINE", "INSUFFICIENT")

RED_FLAGS: dict[str, str] = {
    "face_droop": "One side of my face is drooping or feels numb",
    "arm_weakness": "An arm or leg feels weak or numb",
    "speech_difficulty": "My speech is slurred or I can’t find words",
    "confusion": "Sudden confusion, severe headache, or loss of balance or vision",
    "breathing": "Severe shortness of breath, or lips turning blue",
    "chest_pain": "Chest pain, pressure or tightness",
}

MODERATE_Z = 1.5
STRONG_Z = 3.0
MIN_FACE_SECONDS = 10.0
MIN_VOICE_SECONDS = 5.0


@dataclass(slots=True)
class Measure:
    name: str
    z: float
    usual: str  # plain-language direction, e.g. "higher than usual"


@dataclass(slots=True)
class Finding:
    id: str
    title: str
    severity: str  # "moderate" | "strong"
    summary: str
    measures: list[Measure]
    possible_causes: list[str]


@dataclass(slots=True)
class Assessment:
    triage: str
    headline: str
    advice: list[str]
    findings: list[Finding] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    rules_version: str = RULES_VERSION
    demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------------- helpers
def _z(summary: dict[str, Any], modality: str, index: int) -> float | None:
    values = (summary.get("z") or {}).get(modality)
    if not values or index >= len(values):
        return None
    v = values[index]
    return float(v) if v is not None else None


def _severity(magnitude: float) -> str:
    return "strong" if magnitude >= STRONG_Z else "moderate"


def _dir(z: float) -> str:
    return "higher than usual" if z > 0 else "lower than usual"


# ----------------------------------------------------------------------------- findings
def _findings(summary: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []

    asym = _z(summary, "vision", 0)
    if asym is not None and asym >= MODERATE_Z:
        out.append(Finding(
            id="facial_asymmetry",
            title="Your mouth is less even than usual",
            severity=_severity(asym),
            summary="One corner of the mouth sat further from the mirror image of the other than in your earlier readings.",
            measures=[Measure("Lip-corner asymmetry", asym, _dir(asym))],
            possible_causes=[
                "Stroke or mini-stroke (TIA), especially if it came on suddenly",
                "Bell’s palsy or another problem with the facial nerve",
                "Dental work, a mouth injury, swelling or numbness",
                "Camera angle, lighting or head position during this reading",
            ],
        ))

    lips = _z(summary, "vision", 1)
    if lips is not None and lips <= -MODERATE_Z:
        out.append(Finding(
            id="reduced_mouth_movement",
            title="Smaller mouth movements while speaking",
            severity=_severity(-lips),
            summary="Your lips opened less while reading than they usually do.",
            measures=[Measure("Lip aperture", lips, _dir(lips))],
            possible_causes=[
                "Tiredness, low mood or low energy",
                "Speaking more quietly or more slowly than usual",
                "Side effects of some medicines",
                "Conditions that affect movement, such as Parkinson’s disease",
            ],
        ))

    ear = _z(summary, "vision", 2)
    if ear is not None and ear <= -MODERATE_Z:
        out.append(Finding(
            id="reduced_eye_opening",
            title="Your eyes were less open than usual",
            severity=_severity(-ear),
            summary="Average eye opening was lower than in your earlier readings.",
            measures=[Measure("Eye aspect ratio", ear, _dir(ear))],
            possible_causes=[
                "Tiredness or poor sleep",
                "Eye irritation, allergy or eyelid swelling",
                "Weakness of the muscles or nerves that lift the eyelid",
            ],
        ))

    jitter, shimmer, hnr = _z(summary, "voice", 1), _z(summary, "voice", 2), _z(summary, "voice", 3)
    voice_measures = []
    if jitter is not None and jitter >= MODERATE_Z:
        voice_measures.append(Measure("Jitter (pitch steadiness)", jitter, _dir(jitter)))
    if shimmer is not None and shimmer >= MODERATE_Z:
        voice_measures.append(Measure("Shimmer (loudness steadiness)", shimmer, _dir(shimmer)))
    if hnr is not None and hnr <= -MODERATE_Z:
        voice_measures.append(Measure("Harmonics-to-noise ratio", hnr, _dir(hnr)))
    if voice_measures:
        out.append(Finding(
            id="voice_quality",
            title="Your voice is rougher or breathier than usual",
            severity=_severity(max(abs(m.z) for m in voice_measures)),
            summary="Your vocal folds vibrated less steadily than in your earlier readings.",
            measures=voice_measures,
            possible_causes=[
                "A cold, laryngitis or other throat irritation",
                "Voice strain from shouting, singing or long calls",
                "Acid reflux",
                "Breathing problems such as asthma or a chest infection",
                "Less often, a nerve or muscle condition affecting voice control",
            ],
        ))

    centroid, zcr = _z(summary, "audio", 13), _z(summary, "audio", 14)
    if centroid is not None and zcr is not None and centroid >= MODERATE_Z and zcr >= MODERATE_Z:
        out.append(Finding(
            id="breathy_speech",
            title="More breath noise in your speech",
            severity=_severity(min(centroid, zcr)),
            summary="Your speech carried more high-frequency breath noise than usual.",
            measures=[Measure("Spectral centroid", centroid, _dir(centroid)), Measure("Zero-crossing rate", zcr, _dir(zcr))],
            possible_causes=[
                "Shortness of breath from asthma, COPD or a chest infection",
                "Throat irritation or a hoarse voice",
                "A noisier room or a different microphone position",
            ],
        ))

    f0 = _z(summary, "voice", 0)
    if f0 is not None and abs(f0) >= 2.0:
        out.append(Finding(
            id="pitch_change",
            title=f"Your voice is {'higher' if f0 > 0 else 'lower'} than usual",
            severity="moderate",
            summary="Average speaking pitch moved away from your usual range.",
            measures=[Measure("Pitch (f0)", f0, _dir(f0))],
            possible_causes=["A cold or throat irritation", "Stress, excitement or tiredness", "Hormonal changes"],
        ))

    seconds = max(1.0, float(summary.get("seconds") or 0.0))
    status_s = summary.get("status_seconds") or {}
    elevated_share = float(status_s.get("ELEVATED", 0.0)) / seconds
    watch_share = float(status_s.get("WATCH", 0.0)) / seconds
    if elevated_share >= 0.10 or watch_share + elevated_share >= 0.25:
        out.append(Finding(
            id="coordination",
            title="Your speech and mouth movements were less in step",
            severity="strong" if elevated_share >= 0.25 else "moderate",
            summary=(
                f"The combined voice-and-face reading was raised for {round(100 * (watch_share + elevated_share))}% "
                "of the session."
            ),
            measures=[],
            possible_causes=[
                "Tiredness, alcohol or medicines that cause drowsiness",
                "Stroke or mini-stroke (TIA), especially if it came on suddenly",
                "Conditions that affect the muscles used for speech",
            ],
        ))
    return out


# ----------------------------------------------------------------------------- triage
_ADVICE = {
    "EMERGENCY_REPORTED": (
        "Get emergency help now",
        [
            "Call your local emergency number now (for example 911, 112 or 999).",
            "Tell them which warning signs you have and the time they started.",
            "Don’t drive yourself. Unlock the door and stay where someone can reach you.",
            "Don’t take any medicine for this, including aspirin, unless the emergency service tells you to.",
        ],
    ),
    "EMERGENCY_MEASURED": (
        "Possible stroke warning signs",
        [
            "If your face, speech, arm or leg changed suddenly, call your local emergency number now.",
            "Note the time you were last well and tell the call handler.",
            "Don’t take any medicine for this, including aspirin, unless the emergency service tells you to.",
            "If you’re sure these changes aren’t new, contact your doctor today and show them this result.",
        ],
    ),
    "SEE_DOCTOR": (
        "See a doctor within 24 hours",
        [
            "Book an appointment with your doctor or clinic for today or tomorrow and show them this result.",
            "Do another reading tomorrow at the same time of day.",
            "If any warning sign below appears, call emergency services instead of waiting.",
        ],
    ),
    "DISCUSS": (
        "Mention this at your next appointment",
        [
            "Do another reading tomorrow at the same time of day to see if the change lasts.",
            "If it lasts three days or gets worse, contact your doctor sooner.",
        ],
    ),
    "NO_CHANGE": (
        "No change from your usual",
        ["Keep doing a reading at the same time each day so changes can be spotted early."],
    ),
    "BASELINE": (
        "Still learning your usual",
        [
            "Results compare you with your own earlier readings, so the first sessions set your baseline.",
            "Do a reading each day this week at the same time, reading the passage aloud for at least a minute.",
            "This result is not a check-up. If you feel unwell, contact your doctor.",
        ],
    ),
    "INSUFFICIENT": (
        "Not enough signal for a result",
        [
            "Face the camera in good light and read the passage aloud for at least 30 seconds.",
            "If you feel unwell, contact your doctor; don’t wait for a reading.",
        ],
    ),
}


DEMO_HEADLINE = "Demonstration result"
DEMO_ADVICE = [
    "This is a demo of Flaria. Its analysis model hasn’t been trained or clinically tested, so this result says nothing about your health.",
    "If you notice any of the warning signs listed here, call your local emergency number now, whatever this page shows.",
    "For any health concern, talk to a doctor.",
]


def assess(summary: dict[str, Any], red_flags: Iterable[str] = (), *, demo: bool = False) -> Assessment:
    """Turn a session summary into a result.

    In demo mode, measured results are relabelled as demonstrations: a test model
    must never tell a real person they may be having a stroke, or that they are fine.
    Warning signs the person reports themselves still produce full emergency advice.
    """
    flags = sorted(set(red_flags) & RED_FLAGS.keys())
    face_s = float((summary.get("valid_seconds") or {}).get("face", 0.0))
    voice_s = float((summary.get("valid_seconds") or {}).get("voice", 0.0))
    enough = face_s >= MIN_FACE_SECONDS and voice_s >= MIN_VOICE_SECONDS
    baseline = bool(summary.get("persisted_baseline"))
    findings = _findings(summary) if (baseline and enough) else []
    ids = {f.id: f for f in findings}

    if flags:
        key = "EMERGENCY_REPORTED"
        triage = "EMERGENCY"
    elif "facial_asymmetry" in ids and ids["facial_asymmetry"].severity == "strong" and (
        "coordination" in ids or "voice_quality" in ids or "reduced_mouth_movement" in ids
    ):
        triage, key = "EMERGENCY", "EMERGENCY_MEASURED"
    elif not enough:
        key = triage = "INSUFFICIENT"
    elif not baseline:
        key = triage = "BASELINE"
    elif any(f.severity == "strong" for f in findings):
        key = triage = "SEE_DOCTOR"
    elif findings:
        key = triage = "DISCUSS"
    else:
        key = triage = "NO_CHANGE"

    headline, advice = _ADVICE[key]
    if demo and key != "EMERGENCY_REPORTED":
        headline, advice = DEMO_HEADLINE, DEMO_ADVICE
    return Assessment(triage=triage, headline=headline, advice=list(advice), findings=findings, red_flags=flags,
                      demo=demo)
