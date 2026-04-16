"""
visp_transcribe — transcribe audio files from inside a VISP Jupyter session.

Talks to session-manager over a Unix Domain Socket (api.sock) — no TCP,
no direct WhisperVault access. Model selection and serialization are handled
by session-manager, so concurrent requests from multiple sessions are queued
automatically.

Quick start
-----------
    from visp_transcribe import list_models, transcribe

    # See what models are available
    for m in list_models():
        print(m["id"], "-", m["description"])

    # Transcribe a file and get the text back
    result = transcribe("recordings/speaker1.wav")
    print(result["txt"])

    # With options
    result = transcribe(
        "recordings/speaker1.wav",
        model="kb-whisper",       # Swedish model
        language="Swedish",
        diarize=True,             # separate speakers
        formats=["txt", "srt"],   # get both plain text and subtitles
    )
    print(result["txt"])
    print(result["srt"])

    # Batch loop — transcribe a folder and collect results
    import pathlib
    results = {}
    for wav in sorted(pathlib.Path("recordings").glob("*.wav")):
        results[wav.name] = transcribe(str(wav), model="kb-whisper", language="Swedish")

File paths
----------
Paths can be absolute (/home/jovyan/project/...) or relative to the
project root (just "recordings/speaker1.wav").  Only files inside your
project directory are accessible.

Models
------
Use list_models() to see what is installed on the server.  Common values:
  "whisper"    — Multilingual (faster-whisper large-v3)
  "kb-whisper" — Swedish (KB Whisper large)

Advanced options
----------------
The ``advanced_options`` dict maps to WhisperVault reload-time parameters.
Changing any of these causes the ASR model to reload (takes 3–17 s).

  beam_size                  int, 5–10 (default 5)
  repetition_penalty         float (default 1.3)
  condition_on_previous_text bool (default False)
  vad                        bool — enable voice-activity detection (default True)
  vad_onset                  float, 0.0–1.0 — VAD sensitivity (default 0.3)

Example::

    result = transcribe(
        "interview.wav",
        model="kb-whisper",
        language="Swedish",
        advanced_options={"beam_size": 8, "repetition_penalty": 1.1},
    )
"""

import httpx

_SOCKET_PATH = "/run/session/api.sock"
_BASE_URL = "http://localhost"
# Transcription can take a long time for large files
_TIMEOUT = httpx.Timeout(connect=10, read=3600, write=60, pool=10)


def _client():
    transport = httpx.HTTPTransport(uds=_SOCKET_PATH)
    return httpx.Client(transport=transport, base_url=_BASE_URL, timeout=_TIMEOUT)


def list_models():
    """
    Return the list of available transcription models.

    Returns
    -------
    list of dict
        Each dict has keys: id (str), language (str or None), description (str).

    Example
    -------
    >>> for m in list_models():
    ...     print(m["id"], "-", m["description"])
    whisper - Multilingual (faster-whisper large-v3)
    kb-whisper - Swedish (KB Whisper large)
    """
    with _client() as c:
        resp = c.get("/models")
        _check(resp)
        return resp.json()


def transcribe(
    file,
    model="whisper",
    language=None,
    diarize=False,
    formats=None,
    advanced_options=None,
):
    """
    Transcribe an audio file and return the transcript.

    The call blocks until transcription is complete (which may take a while
    for long files or if other transcriptions are already queued).

    Parameters
    ----------
    file : str
        Path to the audio file.  Absolute paths must start with
        /home/jovyan/project/; relative paths are resolved from there.
        Any format ffmpeg understands is accepted (wav, mp3, flac, ogg, …).
    model : str, optional
        Model ID from list_models(). Default "whisper" (multilingual).
    language : str or None, optional
        Full English language name ("Swedish", "English", …) or None for
        automatic detection.
    diarize : bool, optional
        If True, separate speakers in the output (adds [SPEAKER_00] labels).
    formats : list of str, optional
        Output formats to return.  Any combination of "txt" and "srt".
        Default ["txt"].
    advanced_options : dict or None, optional
        Fine-grained ASR parameters.  Changing these causes WhisperVault to
        reload the model (adds ~3–17 s on first use).

        beam_size (int, 5–10)
            Beam search width.  Higher → more accurate but slower.
        repetition_penalty (float)
            Penalise repeated tokens.  Default 1.3; lower = less penalty.
        condition_on_previous_text (bool)
            Feed previous transcript as prompt context.  Default False.
        vad (bool)
            Enable voice-activity detection to skip silence.  Default True.
        vad_onset (float, 0.0–1.0)
            VAD sensitivity threshold.  Lower = more speech detected.

    Returns
    -------
    dict
        Keys depend on requested formats:
          "txt" — plain text transcript
          "srt" — subtitle file content

    Raises
    ------
    RuntimeError
        If the server returns an error (file not found, model unavailable, …).
    """
    if formats is None:
        formats = ["txt"]

    payload = {
        "file": file,
        "model": model,
        "language": language or "Automatic Detection",
        "diarize": bool(diarize),
        "formats": formats,
        "advancedOptions": _snake_to_camel(advanced_options or {}),
    }

    with _client() as c:
        resp = c.post("/transcribe", json=payload)
        _check(resp)
        return resp.json()


_CAMEL_MAP = {
    "beam_size": "beamSize",
    "repetition_penalty": "repetitionPenalty",
    "condition_on_previous_text": "conditionOnPreviousText",
    "vad": "vad",
    "vad_onset": "vadOnset",
}


def _snake_to_camel(d):
    """Convert snake_case advanced option keys to camelCase for the API."""
    return {_CAMEL_MAP.get(k, k): v for k, v in d.items()}


def _check(resp):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except (ValueError, KeyError):
            detail = resp.text
        raise RuntimeError(f"VISP transcription error {resp.status_code}: {detail}")
