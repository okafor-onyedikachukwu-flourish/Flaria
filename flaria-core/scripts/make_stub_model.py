#!/usr/bin/env python3
"""Build a tiny ONNX graph with the production I/O contract, for CI smoke tests.

    python scripts/make_stub_model.py --out /tmp/stub-model

It maps mean |z| of both modalities to a logit, which is enough to exercise the
server end to end (auth -> WebSocket -> features -> calibration -> inference ->
persistence) on machines without PyTorch. It is NOT a model: the metadata is
flagged so the version string makes the substitution obvious in every session
row and in the dashboard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def build() -> onnx.ModelProto:
    audio = helper.make_tensor_value_info("audio", TensorProto.FLOAT, ["batch", "seq", 15])
    vision = helper.make_tensor_value_info("vision", TensorProto.FLOAT, ["batch", "seq", 5])
    logits = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 1])
    inits = [
        numpy_helper.from_array(np.array([-1, 1], dtype=np.int64), "shape"),
        numpy_helper.from_array(np.array(2.5, dtype=np.float32), "scale"),
        numpy_helper.from_array(np.array(3.0, dtype=np.float32), "offset"),
    ]
    nodes = [
        helper.make_node("Abs", ["audio"], ["abs_a"]),
        helper.make_node("Abs", ["vision"], ["abs_v"]),
        helper.make_node("ReduceMean", ["abs_a"], ["mean_a"], axes=[1, 2], keepdims=1),
        helper.make_node("ReduceMean", ["abs_v"], ["mean_v"], axes=[1, 2], keepdims=1),
        helper.make_node("Add", ["mean_a", "mean_v"], ["sum"]),
        helper.make_node("Mul", ["sum", "scale"], ["scaled"]),
        helper.make_node("Sub", ["scaled", "offset"], ["shifted"]),
        helper.make_node("Reshape", ["shifted", "shape"], ["logits"]),
    ]
    graph = helper.make_graph(nodes, "flaria_stub", [audio, vision], [logits], initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], producer_name="flaria-stub")
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seq-len", type=int, default=48)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "flaria_core.onnx"
    onnx.save(build(), str(path))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "version": f"STUB-NOT-A-MODEL-{sha[:8]}",
        "seq_len": args.seq_len,
        "tick_hz": 30,
        "audio_dim": 15,
        "vision_dim": 5,
        "threshold_watch": 0.6,
        "threshold_elevated": 0.8,
        "sha256": sha,
        "opset": 17,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_data": "none",
        "intended_use": "CI smoke testing only.",
    }
    (args.out / "flaria_core.meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {path} ({meta['version']})")


if __name__ == "__main__":
    main()
