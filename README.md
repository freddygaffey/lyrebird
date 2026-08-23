# Lyrebird

**Offline dictation that gets your words right.**

Named after the Australian bird that reproduces any sound it hears with uncanny
precision — which is the whole job.


Offline speech-to-text with optional local grammar cleanup. No cloud, no subscription,
no audio or text ever leaves the machine.

Built after a real problem: a long document dictated with macOS's built-in dictation
accumulated ~140 transcription errors — `servo` → "server", `louvres` → "luvers",
`CAD` → "card", `revolve` split across a line break. Whisper plus a custom vocabulary
fixes most of that class of error before it reaches the page.

## What it does

```
  F5 pressed
      |
      v
  record mic  ->  faster-whisper (local)  ->  [optional] Ollama LLM (local)  ->  typed into focused field
                  large-v3-turbo                 grammar / filler cleanup
```

Everything runs on your machine. Ollama is optional — turn it off and you get raw
Whisper output, which is already well punctuated and noticeably faster.

## Install (no terminal required)

Download `Lyrebird.dmg`, drag Lyrebird to Applications, open it.

macOS will ask for **Microphone** and **Accessibility** permission. Both are
required: microphone to hear you, accessibility to type for you. The settings
window opens by itself. Press **F5** to dictate.

First launch downloads the speech model (about 1.6 GB). After that it is fully
offline. On an unsigned build macOS may say the developer cannot be verified —
right-click the app and choose **Open**.

Windows: run `Lyrebird-Setup.exe`. Linux: build from source, below.

## Build it yourself

One script does everything, on macOS and Linux alike:

```bash
cd ~/dev/lyrebird
./setup.sh                # install + self-test
./setup.sh --cleanup      # also install Ollama for grammar cleanup
./setup.sh --check        # verify an existing install
```

Windows: `powershell -ExecutionPolicy Bypass -File .\setup.ps1`

Then:

```bash
.venv/bin/python src/dictate.py     # start dictating
.venv/bin/python src/webui.py       # settings page, http://127.0.0.1:5000
```

Press **F5**, talk, press **F5** again. Text appears wherever your cursor is.

## Settings without touching a config file

```bash
.venv/bin/python src/webui.py
```

Opens a plain settings page in your browser: hotkey, accuracy, grammar cleanup,
and your word list, with a live health check at the top. It writes the same config
files, and backs them up before every save. Nothing is exposed to the network —
it binds to `127.0.0.1` only.

## Building the installers

```bash
./build/build-macos.sh                  # -> dist/Lyrebird.app and dist/Lyrebird.dmg
powershell -File build\build-windows.ps1  # -> dist/Lyrebird.exe (+ installer)
```

The bundle is ~118 MB. `torch` is a declared dependency of `mlx-whisper` but is
never used at runtime, so it is excluded — that alone saves 511 MB.

## Platform support

| Platform | Status | Notes |
|---|---|---|
| macOS (Apple Silicon) | primary | Needs Accessibility + Microphone permission |
| macOS (Intel) | works | Slower; use `small` or `medium` model |
| Linux | works | X11 fine. Wayland blocks global hotkeys — see docs/TROUBLESHOOTING.md |
| Windows | works | Run PowerShell as your normal user, not admin |

## Configuration

Everything lives in `config/config.ini` — plain INI, safe to edit by hand.
Custom vocabulary lives in `config/dictionary.txt`, one term per line.

Adding a term to `dictionary.txt` is the single highest-value thing you can do.
It is what stops "servo" becoming "server".

## Where to look when it breaks

- `docs/ARCHITECTURE.md` — how the pieces fit, and why each choice was made
- `docs/MAINTENANCE.md` — updating models, changing hotkeys, routine upkeep
- `docs/TROUBLESHOOTING.md` — symptoms and fixes

## Licence

MIT. See LICENSE.
