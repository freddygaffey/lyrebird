#!/usr/bin/env python3
"""
Transcription backends.

Two engines, one interface:

  mlx ........... Apple Silicon GPU via Metal. Fastest by a wide margin on a Mac.
  ctranslate2 ... faster-whisper. CPU everywhere, plus NVIDIA GPU via CUDA.

`auto` picks MLX on Apple Silicon, CUDA where available, otherwise CPU.
Measured on an M5: MLX 16.7x realtime vs CPU 2.7x. Prefer MLX on a Mac.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Plain model names -> MLX community repos.
MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
    "large-v3": "mlx-community/whisper-large-v3",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
}


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def has_mlx() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return is_apple_silicon()
    except Exception:
        return False


def has_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_backend(requested: str) -> str:
    """Turn 'auto' into a concrete backend name."""
    requested = (requested or "auto").strip().lower()
    if requested != "auto":
        return requested
    if has_mlx():
        return "mlx"
    return "ctranslate2"


def describe_hardware() -> list[tuple[str, str, bool]]:
    """(label, value, available) rows for the UI status panel."""
    rows = []
    if is_apple_silicon():
        rows.append(("Apple Silicon", platform.processor() or "arm64", True))
        rows.append((
            "Metal GPU (MLX)",
            "available — fastest option" if has_mlx() else "not installed (pip install mlx-whisper)",
            has_mlx(),
        ))
    cuda = has_cuda()
    rows.append((
        "NVIDIA GPU (CUDA)",
        "available" if cuda else "not detected",
        cuda,
    ))
    return rows


def _warmup_audio():
    """One second of very faint noise.

    Pure silence makes the mel filterbank take log(0), which spews numpy warnings.
    Faint noise warms the same code paths without them.
    """
    import numpy as np

    rng = np.random.default_rng(0)
    return (rng.standard_normal(16000) * 1e-4).astype("float32")


class Backend:
    """Common interface: .transcribe(float32 mono 16k numpy array) -> str"""

    name = "base"

    def transcribe(self, audio, language: str, initial_prompt: str | None) -> str:
        raise NotImplementedError


class MLXBackend(Backend):
    """Apple Metal via mlx-whisper."""

    name = "mlx"

    def __init__(self, model: str):
        import mlx_whisper  # noqa: F401

        self.repo = MLX_REPOS.get(model, model)
        self._mlx = mlx_whisper

    def transcribe(self, audio, language: str, initial_prompt: str | None) -> str:
        kwargs = {"path_or_hf_repo": self.repo}
        if language and language != "auto":
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        return self._mlx.transcribe(audio, **kwargs)["text"].strip()

    def warm(self) -> None:
        """Download weights and trigger Metal kernel compilation."""
        self.transcribe(_warmup_audio(), "en", None)


class CTranslate2Backend(Backend):
    """faster-whisper: CPU everywhere, CUDA where present."""

    name = "ctranslate2"

    def __init__(self, model: str, device: str = "auto",
                 compute_type: str = "auto", cpu_threads: int = 0):
        from faster_whisper import WhisperModel

        if device == "auto":
            device = "cuda" if has_cuda() else "cpu"
        if compute_type == "auto":
            if device == "cuda":
                compute_type = "float16"
            else:
                # Measured on Apple Silicon: float32 beats int8 (2.7x vs 2.1x).
                compute_type = "float32" if sys.platform == "darwin" else "int8"

        self.device, self.compute_type = device, compute_type
        self.model = WhisperModel(
            model, device=device, compute_type=compute_type, cpu_threads=cpu_threads
        )

    def transcribe(self, audio, language: str, initial_prompt: str | None) -> str:
        kwargs = {"beam_size": 5}
        if language and language != "auto":
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments, _ = self.model.transcribe(audio, **kwargs)
        return " ".join(s.text.strip() for s in segments).strip()

    def warm(self) -> None:
        self.transcribe(_warmup_audio(), "en", None)


def build(backend: str, model: str, device: str = "auto",
          compute_type: str = "auto", cpu_threads: int = 0) -> Backend:
    backend = resolve_backend(backend)
    if backend == "mlx":
        if not has_mlx():
            raise RuntimeError(
                "MLX backend requested but unavailable. "
                "Needs Apple Silicon and `pip install mlx-whisper`."
            )
        return MLXBackend(model)
    if backend == "ctranslate2":
        return CTranslate2Backend(model, device, compute_type, cpu_threads)
    raise ValueError(f"Unknown backend '{backend}' (expected auto, mlx or ctranslate2)")


def load_dictionary() -> str:
    path = paths.config_dir() / "dictionary.txt"
    if not path.exists():
        return ""
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return ", ".join(terms)
