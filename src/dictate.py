#!/usr/bin/env python3
"""
local-dictation — offline push-to-talk / toggle dictation.

Pipeline:  mic -> faster-whisper (local) -> [optional] Ollama (local) -> focused text field

Everything runs on this machine. Nothing is uploaded.

Usage:
    python src/dictate.py              # run the hotkey listener
    python src/dictate.py --check      # diagnose the install, change nothing
    python src/dictate.py --once       # record one utterance, print it, exit
    python src/dictate.py --devices    # list audio input devices
"""
from __future__ import annotations

import argparse
import configparser
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


# --------------------------------------------------------------------------- config
def load_config() -> configparser.ConfigParser:
    """Load config.ini, then overlay config.local.ini if present (gitignored)."""
    cfg = configparser.ConfigParser()
    base = CONFIG_DIR / "config.ini"
    if not base.exists():
        sys.exit(f"Missing config file: {base}")
    cfg.read(base)
    local = CONFIG_DIR / "config.local.ini"
    if local.exists():
        cfg.read(local)
    return cfg


def load_dictionary() -> str:
    """Return dictionary terms as a comma-separated prompt for Whisper.

    Whisper accepts an `initial_prompt` that biases decoding towards the words it
    contains. This is the cheapest, most effective accuracy win available.
    """
    path = CONFIG_DIR / "dictionary.txt"
    if not path.exists():
        return ""
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return ", ".join(terms)


# --------------------------------------------------------------------------- audio
class Recorder:
    """Buffers microphone input until stopped."""

    def __init__(self, sample_rate: int, channels: int, max_seconds: int):
        import numpy as np  # noqa: F401  (imported for side effect of early failure)

        self.sample_rate = sample_rate
        self.channels = channels
        self.max_seconds = max_seconds
        self._frames: list = []
        self._stream = None
        self._started_at = 0.0

    def start(self) -> None:
        import sounddevice as sd

        self._frames = []
        self._started_at = time.time()

        def callback(indata, frames, time_info, status):
            if status:
                print(f"  audio status: {status}", file=sys.stderr)
            self._frames.append(indata.copy())
            if time.time() - self._started_at > self.max_seconds:
                raise sd.CallbackStop()

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            callback=callback,
        )
        self._stream.start()

    def stop(self):
        import numpy as np

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return None
        audio = np.concatenate(self._frames, axis=0)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)          # faster-whisper wants mono
        return audio.astype("float32")


# --------------------------------------------------------------------- transcription
class Transcriber:
    """Wraps faster-whisper. The model is loaded once and reused."""

    def __init__(self, cfg: configparser.ConfigParser):
        from faster_whisper import WhisperModel

        section = cfg["transcription"]
        model_name = section.get("model", "large-v3-turbo")
        device = section.get("device", "auto")
        compute = section.get("compute_type", "auto")

        threads = section.getint("cpu_threads", 0)

        if device == "auto":
            device = "cpu"          # CTranslate2 has no Metal backend; CPU is correct on Mac
        if compute == "auto":
            # float32 measured faster than int8 on Apple Silicon - see config.ini
            compute = "float32" if sys.platform == "darwin" else "int8"

        print(f"Loading model '{model_name}' "
              f"(device={device}, compute={compute}, threads={threads or 'auto'})...")
        t0 = time.time()
        self.model = WhisperModel(
            model_name, device=device, compute_type=compute, cpu_threads=threads
        )
        print(f"Model ready in {time.time() - t0:.1f}s")

        self.language = section.get("language", "en")
        self.initial_prompt = (
            load_dictionary() if section.getboolean("use_dictionary", True) else ""
        )

    def transcribe(self, audio) -> str:
        kwargs = {"beam_size": 5}
        if self.language and self.language != "auto":
            kwargs["language"] = self.language
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt
        segments, _info = self.model.transcribe(audio, **kwargs)
        return " ".join(seg.text.strip() for seg in segments).strip()


# -------------------------------------------------------------------------- cleanup
def clean_with_ollama(text: str, cfg: configparser.ConfigParser) -> str:
    """Optional local LLM pass. Returns the original text on any failure."""
    import requests

    section = cfg["cleanup"]
    if not section.getboolean("enabled", False) or not text:
        return text

    endpoint = section.get("endpoint", "http://localhost:11434").rstrip("/")
    model = section.get("model", "llama3.1:8b")
    timeout = section.getint("timeout_seconds", 30)
    instruction = " ".join(section.get("prompt", "").split())

    try:
        resp = requests.post(
            f"{endpoint}/api/generate",
            json={
                "model": model,
                "prompt": f"{instruction}\n\nText:\n{text}",
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        cleaned = resp.json().get("response", "").strip()
        return cleaned or text
    except Exception as exc:                      # noqa: BLE001 - never lose a transcript
        print(f"  cleanup skipped ({exc.__class__.__name__}: {exc})", file=sys.stderr)
        return text


# --------------------------------------------------------------------------- output
def emit(text: str, cfg: configparser.ConfigParser) -> None:
    if not text:
        return
    section = cfg["output"]
    if section.getboolean("echo_to_stdout", True):
        print(f"> {text}")

    time.sleep(section.getfloat("delay_before_type", 0.15))
    method = section.get("method", "type")

    if method == "clipboard":
        _emit_clipboard(text)
    else:
        from pynput.keyboard import Controller

        Controller().type(text)


def _emit_clipboard(text: str) -> None:
    """Copy then paste, restoring the previous clipboard afterwards."""
    import subprocess

    from pynput.keyboard import Controller, Key

    kb = Controller()
    previous = None
    try:
        if sys.platform == "darwin":
            previous = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            modifier = Key.cmd
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text,
                           text=True, check=True)
            modifier = Key.ctrl
        with kb.pressed(modifier):
            kb.press("v")
            kb.release("v")
        time.sleep(0.15)
    finally:
        if previous is not None and sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=previous, text=True)


# --------------------------------------------------------------------------- runtime
def resolve_key(name: str):
    from pynput.keyboard import Key, KeyCode

    name = name.strip().lower()
    if hasattr(Key, name):
        return getattr(Key, name)
    if len(name) == 1:
        return KeyCode.from_char(name)
    sys.exit(f"Unrecognised hotkey '{name}'. Try f5, f6, f13, cmd_r, alt_r.")


def run_listener(cfg: configparser.ConfigParser) -> None:
    from pynput import keyboard

    audio_cfg = cfg["audio"]
    recorder = Recorder(
        audio_cfg.getint("sample_rate", 16000),
        audio_cfg.getint("channels", 1),
        audio_cfg.getint("max_seconds", 300),
    )
    transcriber = Transcriber(cfg)

    hotkey = resolve_key(cfg["hotkey"].get("key", "f5"))
    mode = cfg["hotkey"].get("mode", "toggle").strip().lower()
    state = {"recording": False, "busy": False}
    lock = threading.Lock()

    def begin():
        with lock:
            if state["recording"] or state["busy"]:
                return
            state["recording"] = True
        print("● recording — press again to stop" if mode == "toggle" else "● recording")
        recorder.start()

    def finish():
        with lock:
            if not state["recording"]:
                return
            state["recording"] = False
            state["busy"] = True
        print("… transcribing")
        try:
            audio = recorder.stop()
            if audio is None or len(audio) < 1600:      # under ~0.1s of audio
                print("  (too short, ignored)")
                return
            t0 = time.time()
            text = transcriber.transcribe(audio)
            took = time.time() - t0
            secs = len(audio) / recorder.sample_rate
            speed = f"{secs / took:.1f}x realtime" if took else "instant"
            print(f"  transcribed {secs:.1f}s of audio in {took:.1f}s ({speed})")
            text = clean_with_ollama(text, cfg)
            emit(text, cfg)
        finally:
            with lock:
                state["busy"] = False

    def on_press(key):
        if key != hotkey:
            return
        if mode == "push_to_talk":
            begin()
        else:
            finish() if state["recording"] else begin()

    def on_release(key):
        if key == hotkey and mode == "push_to_talk":
            finish()

    print(f"\nReady. Hotkey: {cfg['hotkey'].get('key')} ({mode}).  Ctrl+C to quit.\n")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def run_once(cfg: configparser.ConfigParser) -> None:
    audio_cfg = cfg["audio"]
    recorder = Recorder(
        audio_cfg.getint("sample_rate", 16000),
        audio_cfg.getint("channels", 1),
        audio_cfg.getint("max_seconds", 300),
    )
    transcriber = Transcriber(cfg)
    input("Press Enter to start recording...")
    recorder.start()
    input("● recording — press Enter to stop...")
    audio = recorder.stop()
    if audio is None:
        sys.exit("No audio captured.")
    text = transcriber.transcribe(audio)
    print(f"\nRaw:     {text}")
    cleaned = clean_with_ollama(text, cfg)
    if cleaned != text:
        print(f"Cleaned: {cleaned}")


def run_check(cfg: configparser.ConfigParser) -> None:
    print("local-dictation — diagnostics\n")
    ok = True

    print(f"python           {sys.version.split()[0]}")
    print(f"platform         {sys.platform}")

    for module in ("numpy", "sounddevice", "faster_whisper", "pynput", "requests"):
        try:
            __import__(module)
            print(f"  {module:<16} ok")
        except Exception as exc:                  # noqa: BLE001
            print(f"  {module:<16} MISSING ({exc})")
            ok = False

    try:
        import sounddevice as sd

        default_in = sd.query_devices(kind="input")
        print(f"\nmicrophone       {default_in['name']}")
    except Exception as exc:                      # noqa: BLE001
        print(f"\nmicrophone       UNAVAILABLE ({exc})")
        ok = False

    terms = load_dictionary()
    print(f"dictionary       {len(terms.split(',')) if terms else 0} terms")
    print(f"hotkey           {cfg['hotkey'].get('key')} ({cfg['hotkey'].get('mode')})")
    print(f"model            {cfg['transcription'].get('model')}")

    if cfg["cleanup"].getboolean("enabled", False):
        import requests

        endpoint = cfg["cleanup"].get("endpoint").rstrip("/")
        try:
            tags = requests.get(f"{endpoint}/api/tags", timeout=5).json()
            names = [m["name"] for m in tags.get("models", [])]
            wanted = cfg["cleanup"].get("model")
            mark = "ok" if any(n.startswith(wanted.split(":")[0]) for n in names) else "NOT PULLED"
            print(f"ollama           reachable, {len(names)} models, '{wanted}' {mark}")
        except Exception as exc:                  # noqa: BLE001
            print(f"ollama           UNREACHABLE ({exc})")
            ok = False
    else:
        print("cleanup          disabled")

    print("\n" + ("All good." if ok else "Problems found — see docs/TROUBLESHOOTING.md"))
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline local dictation.")
    parser.add_argument("--check", action="store_true", help="diagnose the install")
    parser.add_argument("--once", action="store_true", help="record one utterance and print")
    parser.add_argument("--devices", action="store_true", help="list audio input devices")
    args = parser.parse_args()

    cfg = load_config()

    if args.devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    if args.check:
        run_check(cfg)
        return
    if args.once:
        run_once(cfg)
        return
    run_listener(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
