# ruff: noqa: E402
# %% [markdown]
# # VISP Transcription
#
# This notebook lets you transcribe audio files in your project using WhisperVault —
# the speech-to-text engine built into VISP.
#
# Transcription runs on the server; you just call `transcribe()` and wait for the result.
# Multiple sessions share a queue, so requests from different notebooks are serialised automatically.
#
# ---
# **Quick reference**
#
# | Parameter | Type | Default | Notes |
# |---|---|---|---|
# | `file` | str | — | Path to audio file (relative to project root, or absolute) |
# | `model` | str | `"whisper"` | See `list_models()` |
# | `language` | str\|None | `None` | Full English name, e.g. `"Swedish"`. `None` = auto-detect |
# | `diarize` | bool | `False` | Separate speakers (`[SPEAKER_00]`, `[SPEAKER_01]`, …) |
# | `formats` | list | `["txt"]` | Any combination of `"txt"` and `"srt"` |
# | `advanced_options` | dict | `{}` | See the *Advanced options* section below |

# %% [markdown]
# ## 0 — Find an audio file to work with
#
# Run this cell first. It scans your project for audio files and sets `AUDIO_FILE`
# to the first one found. You can override it manually afterwards.

# %%
import pathlib

_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
_found = sorted(p for p in pathlib.Path(".").rglob("*") if p.suffix.lower() in _AUDIO_EXTENSIONS)

if _found:
    AUDIO_FILE = str(_found[0])
    print(f"Found {len(_found)} audio file(s) in this project:")
    for f in _found[:10]:
        marker = " ← AUDIO_FILE" if f == _found[0] else ""
        print(f"  {f}{marker}")
    if len(_found) > 10:
        print(f"  … and {len(_found) - 10} more")
else:
    AUDIO_FILE = "my_audio.wav"  # set this manually if no files were found
    print("No audio files found in the project yet.")
    print("Set AUDIO_FILE manually, e.g.:  AUDIO_FILE = 'recordings/my_audio.wav'")

# %% [markdown]
# ## 1 — What models are available?

# %%
from visp_transcribe import list_models, transcribe

for m in list_models():
    lang = m.get("language") or "auto-detect"
    print(f"{m['id']:20s}  [{lang:12s}]  {m['description']}")

# %% [markdown]
# ## 2 — Basic transcription
#
# Paths can be relative to the project root or absolute (`/home/jovyan/project/…`).
# Any format ffmpeg understands is accepted: `.wav`, `.mp3`, `.flac`, `.ogg`, …

# %%
result = transcribe(
    AUDIO_FILE,
    model="whisper",  # multilingual model
    language=None,  # None = automatic language detection
)
print(result["txt"])

# %% [markdown]
# ## 3 — Swedish audio with the KB Whisper model

# %%
result = transcribe(
    AUDIO_FILE,
    model="kb-whisper",
    language="Swedish",
)
print(result["txt"])

# %% [markdown]
# ## 4 — Get subtitles (SRT) as well as plain text

# %%
result = transcribe(
    AUDIO_FILE,
    model="kb-whisper",
    language="Swedish",
    formats=["txt", "srt"],
)

print("=== Plain text ===")
print(result["txt"])

print("\n=== SRT subtitles ===")
print(result["srt"])

# Optionally save the SRT file next to the audio:
# pathlib.Path(AUDIO_FILE).with_suffix(".srt").write_text(result["srt"])

# %% [markdown]
# ## 5 — Speaker diarization
#
# Set `diarize=True` to label each segment with a speaker ID (`[SPEAKER_00]`, `[SPEAKER_01]`, …).
# Diarization works for any language and with both models.

# %%
result = transcribe(
    AUDIO_FILE,
    model="kb-whisper",
    language="Swedish",
    diarize=True,
    formats=["txt", "srt"],
)
print(result["txt"])

# %% [markdown]
# ## 6 — Advanced options
#
# These map directly to WhisperVault ASR parameters. **Changing any of them causes the model
# to reload** (adds roughly 3–17 s before the transcription starts). The reload only happens
# when the settings actually change — repeated calls with the same options are fast.
#
# | Key | Type | Range/default | Notes |
# |---|---|---|---|
# | `beam_size` | int | 5–10 (default 5) | Beam search width. Higher = more accurate, slower |
# | `repetition_penalty` | float | 0.5–2.0 (default 1.3) | Penalty for repeated tokens |
# | `condition_on_previous_text` | bool | False | Feed preceding transcript as context |
# | `vad` | bool | True | Voice-activity detection (skip silence) |
# | `vad_onset` | float | 0.0–1.0 (default 0.3) | VAD sensitivity. Lower = more speech detected |

# %%
result = transcribe(
    AUDIO_FILE,
    model="kb-whisper",
    language="Swedish",
    advanced_options={
        "beam_size": 8,
        "repetition_penalty": 1.1,
        "vad": True,
        "vad_onset": 0.4,
        "condition_on_previous_text": False,
    },
)
print(result["txt"])

# %% [markdown]
# ## 7 — Batch transcription
#
# Loop over a folder of files and collect results. Each file is queued separately;
# the loop blocks on each one until it finishes.

# %%
import pathlib

audio_dir = pathlib.Path(".")  # ← change to a subfolder if needed, e.g. Path("recordings")
results = {}

for wav in sorted(audio_dir.rglob("*.wav")):
    print(f"Transcribing {wav} …", end=" ", flush=True)
    try:
        results[str(wav)] = transcribe(
            str(wav),
            model="kb-whisper",
            language="Swedish",
            formats=["txt", "srt"],
        )
        print("done")
    except RuntimeError as e:
        print(f"FAILED: {e}")
        results[str(wav)] = None

print(f"\n{len(results)} files processed")
for name, r in results.items():
    snippet = r["txt"][:80].replace("\n", " ") if r else "(error)"
    print(f"  {name}: {snippet}")

# %% [markdown]
# ## 8 — Save results to files

# %%
import pathlib

# After running the batch cell above, save each transcript alongside the audio
for name, r in results.items():
    if r is None:
        continue
    p = pathlib.Path(name)
    if "txt" in r:
        p.with_suffix(".txt").write_text(r["txt"])
    if "srt" in r:
        p.with_suffix(".srt").write_text(r["srt"])

print("Saved.")

# %% [markdown]
# ---
# ## Full API reference
#
# Run the cell below to see the complete inline documentation.

# %%
import visp_transcribe

help(visp_transcribe)
