#!/usr/bin/env python3
"""
WhisperX Web API Service
Provides REST API for speech transcription with optional speaker diarization.
"""

import os
import tempfile
import logging
from typing import Optional, Dict, Any
from pathlib import Path

import uvicorn
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import torch
import whisperx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WhisperXService:
    """WhisperX transcription service with model caching."""

    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.models: Dict[str, Any] = {}  # Cache loaded models
        self.align_models: Dict[str, Any] = {}  # Cache alignment models

        # Model directories (mounted volumes)
        self.model_dir = Path("/app/models")
        self.output_dir = Path("/app/outputs")

        logger.info(f"Initialized WhisperX service on device: {self.device}")

    def _load_model(self, model_size: str):
        """Load and cache Whisper model."""
        if model_size not in self.models:
            logger.info(f"Loading Whisper model: {model_size}")
            try:
                self.models[model_size] = whisperx.load_model(
                    model_size,
                    device=self.device,
                    download_root=str(self.model_dir),
                    compute_type="float16" if self.device == "cuda" else "int8",
                )
            except Exception as e:
                logger.error(f"Failed to load model {model_size}: {e}")
                raise HTTPException(
                    status_code=500, detail=f"Model loading failed: {str(e)}"
                )

        return self.models[model_size]

    def _load_align_model(self, language: str):
        """Load and cache alignment model for language."""
        if language not in self.align_models:
            logger.info(f"Loading alignment model for language: {language}")
            try:
                model, metadata = whisperx.load_align_model(
                    language_code=language, device=self.device
                )
                self.align_models[language] = (model, metadata)
            except Exception as e:
                logger.warning(f"Failed to load alignment model for {language}: {e}")
                return None, None

        return self.align_models[language]

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        model_size: str = "base",
        enable_diarization: bool = False,
        hf_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe audio file with optional diarization."""

        # Validate inputs
        if not Path(audio_path).exists():
            raise HTTPException(status_code=400, detail="Audio file not found")

        if model_size not in [
            "tiny",
            "base",
            "small",
            "medium",
            "large",
            "large-v2",
            "large-v3",
        ]:
            raise HTTPException(
                status_code=400, detail=f"Invalid model size: {model_size}"
            )

        # Load Whisper model
        model = self._load_model(model_size)

        # Load audio
        logger.info(f"Loading audio: {audio_path}")
        audio = whisperx.load_audio(audio_path)

        # Transcribe
        logger.info("Starting transcription...")
        result = model.transcribe(audio, language=language, batch_size=16)

        detected_language = result["language"]
        logger.info(f"Detected language: {detected_language}")

        # Align for word-level timestamps
        align_model, metadata = self._load_align_model(detected_language)
        if align_model and metadata:
            logger.info("Aligning transcription for word-level timestamps...")
            result = whisperx.align(
                result["segments"], align_model, metadata, audio, self.device
            )

        # Speaker diarization (requires HuggingFace token)
        if enable_diarization:
            if not hf_token:
                logger.warning("Diarization requested but no HF_TOKEN provided")
                result["diarization_warning"] = (
                    "No HuggingFace token provided for diarization"
                )
            else:
                try:
                    logger.info("Starting speaker diarization...")
                    diarize_model = whisperx.DiarizationPipeline(
                        use_auth_token=hf_token, device=self.device
                    )
                    diarize_segments = diarize_model(audio)
                    result = whisperx.assign_word_speakers(diarize_segments, result)
                    logger.info("Diarization completed")
                except Exception as e:
                    logger.error(f"Diarization failed: {e}")
                    result["diarization_error"] = str(e)

        # Persist outputs to disk in the mounted output directory (JSON, plain text, SRT)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            base_name = Path(audio_path).stem
            timestamp = int(time.time())
            fname_base = f"{base_name}-{timestamp}"

            # Save full JSON
            out_json = self.output_dir / f"{fname_base}.json"
            with open(out_json, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)

            # Save plain text (concatenate segments)
            segments = result.get("segments", []) or []
            plain_text = "\n".join([s.get("text", "").strip() for s in segments])
            out_txt = self.output_dir / f"{fname_base}.txt"
            out_txt.write_text(plain_text, encoding="utf-8")

            # Save SRT
            def _srt_time(sec: float) -> str:
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                s = int(sec % 60)
                ms = int((sec - int(sec)) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

            srt_lines = []
            for i, seg in enumerate(segments, start=1):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                text = seg.get("text", "").strip()
                srt_lines.append(
                    f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n"
                )

            out_srt = self.output_dir / f"{fname_base}.srt"
            out_srt.write_text("\n".join(srt_lines), encoding="utf-8")

            logger.info(
                f"Saved transcription outputs: {out_json}, {out_txt}, {out_srt}"
            )
        except Exception as e:
            logger.warning(f"Failed to persist outputs: {e}")

        return result


# Initialize FastAPI app
app = FastAPI(
    title="WhisperX API",
    description="Speech transcription API using WhisperX",
    version="1.0.0",
)

# Initialize service
service = WhisperXService()


@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    model: str = Form("base"),
    diarize: bool = Form(False),
    hf_token: Optional[str] = Form(None),
):
    """
    Transcribe audio file.

    - **file**: Audio file (wav, mp3, m4a, etc.)
    - **language**: Language code (optional, auto-detect if not provided)
    - **model**: Model size (tiny, base, small, medium, large, large-v2, large-v3)
    - **diarize**: Enable speaker diarization (requires HF_TOKEN)
    - **hf_token**: HuggingFace token for diarization models
    """

    # Validate file type
    allowed_extensions = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
        )

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as temp_file:
        temp_path = temp_file.name
        content = await file.read()
        temp_file.write(content)

    try:
        # Transcribe
        result = await service.transcribe(
            temp_path,
            language=language,
            model_size=model,
            enable_diarization=diarize,
            hf_token=hf_token,
        )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    finally:
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except OSError as e:
            # If the temp file is already removed or inaccessible, log debug and continue
            logger.debug(f"Could not remove temp file {temp_path}: {e}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "device": service.device,
        "cuda_available": torch.cuda.is_available(),
        "cached_models": list(service.models.keys()),
        "cached_align_models": list(service.align_models.keys()),
    }


@app.get("/models")
async def list_models():
    """List available models and cached status."""
    return {
        "available_models": [
            "tiny",
            "base",
            "small",
            "medium",
            "large",
            "large-v2",
            "large-v3",
        ],
        "cached_models": list(service.models.keys()),
        "model_directory": str(service.model_dir),
        "output_directory": str(service.output_dir),
    }


@app.get("/downloaded-models")
async def list_downloaded_models():
    """List actually downloaded model files in the model directory."""
    try:
        model_files = []
        if service.model_dir.exists():
            # Scan for common model file patterns
            for pattern in [
                "*.bin",
                "*.pt",
                "*.pth",
                "*.onnx",
                "*.model",
                "*.safetensors",
            ]:
                model_files.extend(service.model_dir.rglob(pattern))

            # Also look for directories that might contain models
            model_dirs = [d for d in service.model_dir.iterdir() if d.is_dir()]

            return {
                "model_directory": str(service.model_dir),
                "downloaded_files": [
                    str(f.relative_to(service.model_dir)) for f in model_files
                ],
                "model_directories": [
                    str(d.relative_to(service.model_dir)) for d in model_dirs
                ],
                "total_files": len(model_files),
                "total_directories": len(model_dirs),
            }
        else:
            return {
                "model_directory": str(service.model_dir),
                "error": "Model directory does not exist",
                "downloaded_files": [],
                "model_directories": [],
                "total_files": 0,
                "total_directories": 0,
            }
    except Exception as e:
        logger.error(f"Error listing downloaded models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list models: {str(e)}")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
