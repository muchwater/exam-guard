"""Inference runners: QNN HTP (NPU, default) with ONNX Runtime CPU fallback.

Both expose the same call:   outputs = runner.run(x_nchw_float32)  -> list[np.ndarray]
and `input_hw` (model input height, width) read from the model itself.

QNN path: DLC (converted on the PC with QAIRT) -> context binary built on the board with
qnn-context-binary-generator -> kept resident by vision/native/qnn_runner (persistent
process, tensors shared through /dev/shm). Adapted from the team's sd-moe/edge_sd/device_runner.py.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)
NATIVE_DIR = Path(__file__).resolve().parent / "native"

# QNN data type codes (QnnTypes.h) -> numpy dtype; quantized types use scale/offset
_NP = {0x0416: np.uint16, 0x0408: np.uint8, 0x0232: np.float32, 0x0216: np.float16, 0x0032: np.int32}
_QMAX = {0x0416: 65535, 0x0408: 255}


class OnnxCpuRunner:
    name = "onnx-cpu"

    def __init__(self, onnx_path: str | Path, threads: Optional[int] = None):
        import onnxruntime as ort
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.inp = inp.name
        self.input_hw = (int(inp.shape[2]), int(inp.shape[3]))   # NCHW export

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        return [np.asarray(y) for y in self.sess.run(None, {self.inp: x.astype(np.float32)})]

    def close(self) -> None:
        pass


class QnnHtpRunner:
    """Single-graph wrapper around the persistent qnn_runner process."""

    name = "qnn-htp"

    def __init__(self, ctx_path: str | Path, exe: Optional[str | Path] = None, shm_prefix: str = "/dev/shm/eg"):
        exe = str(exe or NATIVE_DIR / "qnn_runner")
        if not os.path.exists(exe):
            raise FileNotFoundError(f"{exe} not built (see vision/native/README.md)")
        self.p = subprocess.Popen([exe, shm_prefix, str(ctx_path)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        line = self.p.stdout.readline()
        while line and not line.startswith("{"):
            line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(f"qnn_runner exited ({self.p.wait()})")
        g = json.loads(line)["graphs"][0]
        for t in g["inputs"] + g["outputs"]:
            t["np"] = np.memmap(t["shm"], dtype=_NP[t["dtype"]], mode="r+", shape=tuple(t["dims"]))
        self.inputs, self.outputs = g["inputs"], g["outputs"]
        dims = self.inputs[0]["dims"]
        self.nhwc = len(dims) == 4 and dims[3] == 3 and dims[1] != 3
        self.input_hw = (dims[1], dims[2]) if self.nhwc else (dims[2], dims[3])
        self.last_execute_us: Optional[int] = None

    @staticmethod
    def _set(t: dict, x: np.ndarray) -> None:
        if t["dtype"] in _QMAX:
            q = np.rint(np.asarray(x, np.float32) / t["scale"]) - t["offset"]
            t["np"][...] = np.clip(q, 0, _QMAX[t["dtype"]]).astype(t["np"].dtype)
        else:
            t["np"][...] = x

    @staticmethod
    def _get(t: dict) -> np.ndarray:
        if t["dtype"] in _QMAX:
            return (t["np"].astype(np.float32) + t["offset"]) * t["scale"]
        return np.array(t["np"], dtype=np.float32)

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        self._set(self.inputs[0], x.transpose(0, 2, 3, 1) if self.nhwc else x)
        self.p.stdin.write("x 0\n")
        while True:
            r = self.p.stdout.readline().split()
            if not r:
                raise RuntimeError("qnn_runner closed")
            if r[0] == "ok":
                self.last_execute_us = int(r[2])
                break
            if r[0] == "err":
                raise RuntimeError(f"graphExecute failed: {r}")
        return [self._get(t) for t in self.outputs]

    def close(self) -> None:
        if self.p.poll() is None:
            self.p.stdin.write("q\n")
            self.p.stdin.flush()
            self.p.wait()


def build_runner(cfg: dict):
    """NPU first; on any failure fall back to CPU and say so."""
    v = cfg["vision"]["pose"]
    backend = v.get("backend", "qnn")
    if backend == "qnn":
        try:
            r = QnnHtpRunner(v["qnn_context"])
            log.info("pose backend: QNN HTP (%s)", v["qnn_context"])
            return r
        except Exception as e:  # noqa: BLE001 - any NPU failure must not stop the demo
            log.warning("QNN HTP unavailable (%s); falling back to ONNX Runtime CPU", e)
    r = OnnxCpuRunner(v["onnx"], v.get("cpu_threads"))
    log.info("pose backend: ONNX Runtime CPU (%s)", v["onnx"])
    return r
