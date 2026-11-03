"""Facial-geometry biomarkers from the 468-point MediaPipe Face Mesh.

5-D vector (model input):
  0 lip_asymmetry      distance between the two lip corners after reflecting one
                       across the facial midline (nasal bridge 168 -> subnasale 2),
                       normalised by inter-ocular distance (IOD). 0 = symmetric.
  1 lip_vertical       inner-lip aperture (13 <-> 14) / IOD
  2 ear                mean Eye Aspect Ratio of both eyes (Soukupova & Cech 2016)
  3 head_pitch_deg     pitch proxy from the face-plane normal
  4 head_yaw_deg       yaw proxy from the face-plane normal

Coordinates arrive normalised to [0, 1] by image width/height (z scaled like
x). They are converted to pixel-isotropic space before any distance is taken,
otherwise non-square frames distort every ratio.

Quality gating rejects frames that would produce misleading geometry: missing
or non-finite landmarks, implausible face size, or head pose outside the range
where the 2-D asymmetry estimate is trustworthy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

N_LANDMARKS = 468
VISION_DIM = 5
VISION_FEATURE_NAMES: tuple[str, ...] = ("lip_asymmetry", "lip_vertical", "ear", "head_pitch_deg", "head_yaw_deg")

# MediaPipe canonical face-mesh indices.
NASAL_BRIDGE = 168
SUBNASALE = 2
LIP_CORNER_R = 61   # subject's right
LIP_CORNER_L = 291  # subject's left
LIP_INNER_UPPER = 13
LIP_INNER_LOWER = 14
EYE_OUTER_R = 33
EYE_OUTER_L = 263
FOREHEAD = 10
CHIN = 152
RIGHT_EYE_EAR = (33, 160, 158, 133, 153, 144)  # p1..p6
LEFT_EYE_EAR = (362, 385, 387, 263, 373, 380)

MAX_ABS_YAW_DEG = 30.0
MAX_ABS_PITCH_DEG = 25.0
MIN_IOD_PX = 18.0


@dataclass(frozen=True, slots=True)
class VisionFeatures:
    vector: np.ndarray  # (5,) float32
    face_present: bool
    pose_ok: bool
    valid: bool  # usable for inference (face present, sane geometry, pose in range)

    @staticmethod
    def absent() -> "VisionFeatures":
        return VisionFeatures(np.zeros(VISION_DIM, np.float32), False, False, False)


def _ear(p: np.ndarray, idx: tuple[int, int, int, int, int, int]) -> float:
    p1, p2, p3, p4, p5, p6 = (p[i] for i in idx)
    horizontal = float(np.linalg.norm(p1[:2] - p4[:2]))
    if horizontal < 1e-6:
        return float("nan")
    return (float(np.linalg.norm(p2[:2] - p6[:2])) + float(np.linalg.norm(p3[:2] - p5[:2]))) / (2.0 * horizontal)


class VisionFeatureExtractor:
    def extract(self, landmarks: np.ndarray, width: int, height: int) -> VisionFeatures:
        """``landmarks``: (468, 3) normalised MediaPipe coordinates."""
        if landmarks is None or width <= 0 or height <= 0:
            return VisionFeatures.absent()
        lm = np.asarray(landmarks, dtype=np.float64)
        if lm.shape != (N_LANDMARKS, 3) or not np.all(np.isfinite(lm)):
            return VisionFeatures.absent()
        if np.any(lm[:, :2] < -0.5) or np.any(lm[:, :2] > 1.5):
            return VisionFeatures.absent()

        p = np.empty_like(lm)
        p[:, 0] = lm[:, 0] * width
        p[:, 1] = lm[:, 1] * height
        p[:, 2] = lm[:, 2] * width

        iod = float(np.linalg.norm(p[EYE_OUTER_L, :2] - p[EYE_OUTER_R, :2]))
        if iod < MIN_IOD_PX:
            return VisionFeatures.absent()

        # --- lip-corner asymmetry about the nasal midline (2-D, image plane)
        m0, m1 = p[NASAL_BRIDGE, :2], p[SUBNASALE, :2]
        axis = m1 - m0
        axis_len = float(np.linalg.norm(axis))
        if axis_len < 1e-6:
            return VisionFeatures.absent()
        u = axis / axis_len  # unit vector down the midline
        n = np.array([-u[1], u[0]])  # unit normal
        cr, cl = p[LIP_CORNER_R, :2] - m0, p[LIP_CORNER_L, :2] - m0
        # Reflect the right corner across the midline and compare with the left.
        cr_reflected = (cr @ u) * u - (cr @ n) * n
        lip_asym = float(np.linalg.norm(cl - cr_reflected)) / iod

        lip_vertical = float(np.linalg.norm(p[LIP_INNER_UPPER] - p[LIP_INNER_LOWER])) / iod

        ear = np.nanmean([_ear(p, RIGHT_EYE_EAR), _ear(p, LEFT_EYE_EAR)])

        # --- head pose proxies from the face-plane normal
        v_eyes = p[EYE_OUTER_L] - p[EYE_OUTER_R]
        v_vertical = p[CHIN] - p[FOREHEAD]
        normal = np.cross(v_eyes, v_vertical)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-9:
            return VisionFeatures.absent()
        normal /= norm
        yaw = math.degrees(math.atan2(normal[0], normal[2]))
        pitch = math.degrees(math.atan2(-normal[1], normal[2]))

        vec = np.array([lip_asym, lip_vertical, ear, pitch, yaw], dtype=np.float32)
        if not np.all(np.isfinite(vec)):
            return VisionFeatures.absent()
        pose_ok = abs(yaw) <= MAX_ABS_YAW_DEG and abs(pitch) <= MAX_ABS_PITCH_DEG
        return VisionFeatures(vec, True, pose_ok, pose_ok)
