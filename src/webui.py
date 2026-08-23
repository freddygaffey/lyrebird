#!/usr/bin/env python3
"""
local-dictation — browser-based configuration.

A small Flask app so the settings can be changed without touching an INI file.
Binds to 127.0.0.1 only: it is never reachable from the network.

    python src/webui.py            # http://127.0.0.1:5000
    python src/webui.py --port 8080
"""
from __future__ import annotations

import argparse
import configparser
import shutil
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, url_for

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "config.ini"
DICTIONARY = ROOT / "config" / "dictionary.txt"
BACKUPS = ROOT / "config" / "backups"

app = Flask(__name__)

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3"]
KEYS = ["f5", "f6", "f7", "f8", "f13", "f14", "cmd_r", "alt_r", "ctrl_r"]


def read_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG)
    return cfg


def backup_config() -> None:
    """Keep a timestamped copy before every write, so a bad edit is never fatal."""
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(CONFIG, BACKUPS / f"config-{stamp}.ini")
    if DICTIONARY.exists():
        shutil.copy2(DICTIONARY, BACKUPS / f"dictionary-{stamp}.txt")
    # keep only the 20 most recent
    for old in sorted(BACKUPS.glob("config-*.ini"))[:-20]:
        old.unlink(missing_ok=True)
    for old in sorted(BACKUPS.glob("dictionary-*.txt"))[:-20]:
        old.unlink(missing_ok=True)


def diagnostics() -> list[tuple[str, str, bool]]:
    """(label, value, healthy) rows for the status panel."""
    rows = []
    rows.append(("Python", sys.version.split()[0], True))
    for mod, label in [
        ("faster_whisper", "Speech engine"),
        ("sounddevice", "Microphone library"),
        ("pynput", "Keyboard control"),
    ]:
        try:
            __import__(mod)
            rows.append((label, "installed", True))
        except Exception:
            rows.append((label, "MISSING — re-run setup.sh", False))
    try:
        import sounddevice as sd

        rows.append(("Microphone", sd.query_devices(kind="input")["name"], True))
    except Exception as exc:
        rows.append(("Microphone", f"unavailable ({exc.__class__.__name__})", False))

    cfg = read_config()
    if cfg.has_section("cleanup") and cfg["cleanup"].getboolean("enabled", False):
        try:
            import requests

            endpoint = cfg["cleanup"].get("endpoint", "").rstrip("/")
            models = requests.get(f"{endpoint}/api/tags", timeout=3).json().get("models", [])
            rows.append(("Grammar cleanup", f"Ollama reachable, {len(models)} models", True))
        except Exception:
            rows.append(("Grammar cleanup", "Ollama not reachable — is it running?", False))
    else:
        rows.append(("Grammar cleanup", "turned off", True))
    return rows


TEMPLATE = """
<!doctype html><meta charset="utf-8">
<title>Dictation Settings</title>
<style>
 :root{--bg:#fbfbfd;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--acc:#0071e3;--card:#fff}
 @media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f5f5f7;--mut:#98989d;--line:#38383c;--card:#1f1f22}}
 *{box-sizing:border-box}
 body{margin:0;padding:2rem 1rem 4rem;background:var(--bg);color:var(--fg);
      font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
 .wrap{max-width:760px;margin:0 auto}
 h1{font-size:1.6rem;margin:0 0 .25rem}
 .sub{color:var(--mut);margin:0 0 2rem}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
       padding:1.25rem 1.5rem;margin-bottom:1.25rem}
 h2{font-size:1.05rem;margin:0 0 .35rem}
 .hint{color:var(--mut);font-size:.87rem;margin:0 0 1rem}
 label{display:block;font-weight:600;font-size:.9rem;margin:1rem 0 .3rem}
 label:first-of-type{margin-top:0}
 .fh{font-weight:400;color:var(--mut);font-size:.85rem;display:block;margin-top:.15rem}
 select,input[type=text],input[type=number],textarea{
   width:100%;padding:.55rem .7rem;border:1px solid var(--line);border-radius:8px;
   background:var(--bg);color:var(--fg);font:inherit;font-size:.95rem}
 textarea{min-height:260px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85rem}
 .row{display:flex;gap:1rem;flex-wrap:wrap}.row>div{flex:1;min-width:190px}
 .check{display:flex;align-items:flex-start;gap:.6rem;margin:1rem 0 0}
 .check input{margin-top:.3rem}.check label{margin:0}
 table{width:100%;border-collapse:collapse;font-size:.9rem}
 td{padding:.4rem 0;border-bottom:1px solid var(--line)}
 td:first-child{color:var(--mut);width:42%}
 .good{color:#1a7f37;font-weight:600}.bad{color:#c00;font-weight:600}
 button{background:var(--acc);color:#fff;border:0;border-radius:8px;
        padding:.7rem 1.4rem;font:inherit;font-weight:600;cursor:pointer}
 .saved{background:#1a7f37;color:#fff;padding:.7rem 1rem;border-radius:8px;margin-bottom:1.25rem}
 code{background:var(--bg);border:1px solid var(--line);padding:.1rem .35rem;border-radius:4px;font-size:.85em}
</style>
<div class="wrap">
<h1>Dictation Settings</h1>
<p class="sub">Changes are saved to your config files. Restart dictation to apply them.</p>

{% if saved %}<div class="saved">Saved. Restart the dictation program for changes to take effect.</div>{% endif %}

<div class="card">
  <h2>Status</h2>
  <p class="hint">A quick health check of the parts that have to work.</p>
  <table>
  {% for label, value, healthy in diags %}
    <tr><td>{{ label }}</td><td class="{{ 'good' if healthy else 'bad' }}">{{ value }}</td></tr>
  {% endfor %}
  </table>
</div>

<form method="post" action="{{ url_for('save') }}">
<div class="card">
  <h2>How you start dictating</h2>
  <p class="hint">Pick the key you press to begin talking.</p>
  <div class="row">
    <div><label>Key<select name="hotkey_key">
      {% for k in keys %}<option value="{{k}}" {{ 'selected' if cfg['hotkey']['key']==k }}>{{k.upper()}}</option>{% endfor %}
    </select></label></div>
    <div><label>Style<select name="hotkey_mode">
      <option value="toggle" {{ 'selected' if cfg['hotkey']['mode']=='toggle' }}>Press once to start, once to stop</option>
      <option value="push_to_talk" {{ 'selected' if cfg['hotkey']['mode']=='push_to_talk' }}>Hold down while talking</option>
    </select></label></div>
  </div>
</div>

<div class="card">
  <h2>Accuracy</h2>
  <p class="hint">Bigger models are more accurate but slower. <code>large-v3-turbo</code> is the best balance on an Apple Silicon Mac.</p>
  <label>Speech model<select name="model">
    {% for m in models %}<option value="{{m}}" {{ 'selected' if cfg['transcription']['model']==m }}>{{m}}</option>{% endfor %}
  </select></label>
  <label>Language <span class="fh">Use <code>en</code> for English. <code>auto</code> is slower and less accurate.</span>
    <input type="text" name="language" value="{{ cfg['transcription']['language'] }}"></label>
  <div class="check">
    <input type="checkbox" id="ud" name="use_dictionary" {{ 'checked' if cfg['transcription'].getboolean('use_dictionary') }}>
    <label for="ud">Use my word list <span class="fh">Strongly recommended. This is what stops "servo" becoming "server".</span></label>
  </div>
</div>

<div class="card">
  <h2>Grammar cleanup</h2>
  <p class="hint">Optionally tidy grammar and remove "um" and "uh" using a second AI model that also runs on this computer. Adds a few seconds before the text appears.</p>
  <div class="check">
    <input type="checkbox" id="ce" name="cleanup_enabled" {{ 'checked' if cfg['cleanup'].getboolean('enabled') }}>
    <label for="ce">Turn on grammar cleanup <span class="fh">Requires Ollama to be installed and running.</span></label>
  </div>
  <label>Cleanup model<input type="text" name="cleanup_model" value="{{ cfg['cleanup']['model'] }}"></label>
</div>

<div class="card">
  <h2>Where the text goes</h2>
  <label>Method<select name="output_method">
    <option value="type" {{ 'selected' if cfg['output']['method']=='type' }}>Type it out (works everywhere)</option>
    <option value="clipboard" {{ 'selected' if cfg['output']['method']=='clipboard' }}>Paste it (faster for long text)</option>
  </select></label>
</div>

<div class="card">
  <h2>My word list</h2>
  <p class="hint">One word or phrase per line. Add anything unusual you say often — technical terms, product names, people's names. Lines starting with <code>#</code> are notes and are ignored.</p>
  <textarea name="dictionary" spellcheck="false">{{ dictionary }}</textarea>
</div>

<button type="submit">Save settings</button>
</form>
</div>
"""


@app.get("/")
def index():
    return render_template_string(
        TEMPLATE,
        cfg=read_config(),
        dictionary=DICTIONARY.read_text(encoding="utf-8") if DICTIONARY.exists() else "",
        diags=diagnostics(),
        models=MODELS,
        keys=KEYS,
        saved=request.args.get("saved") == "1",
    )


@app.post("/save")
def save():
    backup_config()
    cfg = read_config()
    f = request.form

    cfg["hotkey"]["key"] = f.get("hotkey_key", "f5")
    cfg["hotkey"]["mode"] = f.get("hotkey_mode", "toggle")
    cfg["transcription"]["model"] = f.get("model", "large-v3-turbo")
    cfg["transcription"]["language"] = f.get("language", "en").strip() or "en"
    cfg["transcription"]["use_dictionary"] = str("use_dictionary" in f).lower()
    cfg["cleanup"]["enabled"] = str("cleanup_enabled" in f).lower()
    cfg["cleanup"]["model"] = f.get("cleanup_model", "llama3.1:8b").strip()
    cfg["output"]["method"] = f.get("output_method", "type")

    with CONFIG.open("w", encoding="utf-8") as fh:
        cfg.write(fh)
    DICTIONARY.write_text(f.get("dictionary", ""), encoding="utf-8")
    return redirect(url_for("index", saved="1"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser settings for local-dictation.")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    print(f"\n  Settings page:  http://127.0.0.1:{args.port}\n  Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
