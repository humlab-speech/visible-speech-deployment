# WhisperX Web API Container

This container provides a minimal, containerized WhisperX service with a REST API for speech transcription, word-level timestamp alignment, and optional speaker diarization.

## Overview

Unlike the existing Whisper-WebUI container, this service:
- ✅ Provides a clean REST API instead of a web interface
- ✅ Is minimal and focused on transcription tasks
- ✅ Supports model caching and GPU acceleration
- ✅ Includes proper error handling and health checks
- ✅ Can be easily integrated with other services

## Architecture

### Multi-stage Build
- **Builder stage**: Installs all dependencies including PyTorch with CUDA support
- **Runtime stage**: Minimal Python image with only necessary runtime dependencies

### Model Caching
- Whisper models are cached in `/app/models` (mounted volume)
- Alignment models are cached in memory
- Diarization models require HuggingFace authentication

## API Endpoints

### POST /transcribe
Transcribe an audio file.

**Parameters:**
- `file` (required): Audio file upload (wav, mp3, m4a, flac, ogg, webm)
- `language` (optional): Language code (auto-detected if not provided)
- `model` (optional): Model size - `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, `large-v3` (default: `base`)
- `diarize` (optional): Enable speaker diarization (default: `false`)
- `hf_token` (optional): HuggingFace token for diarization models

**Response:**
```json
{
  "language": "en",
  "segments": [
    {
      "text": "Hello world",
      "start": 0.0,
      "end": 2.0,
      "words": [
        {"word": "Hello", "start": 0.0, "end": 1.0},
        {"word": "world", "start": 1.0, "end": 2.0}
      ]
    }
  ]
}
```

### GET /health
Health check endpoint returning service status and cached models.

### GET /models
List available models and caching status.

### GET /downloaded-models
List actually downloaded model files in the mounted model directory.

**Response:**
```json
{
  "model_directory": "/app/models",
  "downloaded_files": [
    "whisper/tiny.bin",
    "whisper/base.bin"
  ],
  "model_directories": [
    "whisper",
    "alignment"
  ],
  "total_files": 2,
  "total_directories": 2
}
```

## Docker Configuration

### Build
```bash
cd docker/whisperx
docker build -t visp-whisperx .
```

### Run
```bash
docker run -d \
  --name whisperx \
  -p 8000:8000 \
  -v ./mounts/whisperx/models:/app/models \
  -v ./mounts/whisperx/outputs:/app/outputs \
  -e HF_TOKEN=your_huggingface_token \
  --gpus all \
  visp-whisperx
```

### Docker Compose Integration
```yaml
whisperx:
  build: ./docker/whisperx
  volumes:
    - ./mounts/whisperx/models:/app/models
    - ./mounts/whisperx/outputs:/app/outputs
  environment:
    - HF_TOKEN=${HF_TOKEN}
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  networks:
    - internal-net
  ports:
    - "8000:8000"
```

## Model Management

### Mounted Volumes
- `/app/models`: Persistent storage for Whisper and alignment models
- `/app/outputs`: Storage for transcription outputs (if needed)

### Diarization Setup
Speaker diarization requires:
1. HuggingFace account and token
2. `pyannote/speaker-diarization` model access (request from HuggingFace)
3. Set `HF_TOKEN` environment variable

## Performance Considerations

### GPU Support
- Automatically detects CUDA availability
- Uses `float16` precision on GPU, `int8` on CPU
- Batch size of 16 for optimal performance

### Model Caching
- Models are loaded once and cached in memory
- Subsequent requests use cached models
- Reduces latency for repeated use

### Resource Limits
- No built-in resource limits (configure in docker-compose)
- Monitor GPU memory usage with large models

## Usage Examples

### Basic Transcription
```bash
curl -X POST \
  -F "file=@audio.wav" \
  -F "model=base" \
  http://localhost:8000/transcribe
```

### Transcription with Diarization
```bash
curl -X POST \
  -F "file=@meeting.wav" \
  -F "model=large-v2" \
  -F "diarize=true" \
  -F "hf_token=your_token" \
  http://localhost:8000/transcribe
```

### Python Client
```python
import requests

with open('audio.wav', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/transcribe',
        files={'file': f},
        data={'model': 'base', 'language': 'en'}
    )

result = response.json()
print(result['segments'][0]['text'])
```

## Future Improvements

### Short Term
- [ ] Add audio format validation
- [ ] Implement request queuing for high load
- [ ] Add metrics/monitoring endpoints
- [ ] Support for streaming audio input
- [ ] Add authentication/authorization

### Medium Term
- [ ] Add model warm-up on startup
- [ ] Implement model versioning and updates
- [ ] Add support for custom alignment models
- [ ] Integrate with VISP session manager
- [ ] Add transcription result caching

### Long Term
- [ ] Support for real-time streaming transcription
- [ ] Multi-language batch processing
- [ ] Integration with external storage (S3, etc.)
- [ ] Advanced diarization features (speaker naming, etc.)
- [ ] Performance optimization for edge devices

## Dependencies

Based on WhisperX pyproject.toml:
- `torch~=2.8.0`, `torchaudio~=2.8.0`
- `faster-whisper>=1.1.1`
- `pyannote-audio>=3.3.2,<4.0.0` (for diarization)
- `transformers>=4.48.0`
- `ctranslate2>=4.5.0`

## Troubleshooting

### Common Issues

**CUDA out of memory:**
- Use smaller model (`base` instead of `large`)
- Reduce batch size in code
- Add GPU memory limits in docker-compose

**Diarization fails:**
- Verify HF_TOKEN is set correctly
- Ensure access to pyannote/speaker-diarization model
- Check HuggingFace account permissions

**Model download fails:**
- Check network connectivity
- Verify model directory permissions
- Ensure sufficient disk space

### Logs
```bash
# View container logs
docker logs whisperx

# Follow logs
docker logs -f whisperx
```

## Security Notes

- No authentication implemented (add as needed)
- File uploads are temporary and cleaned up
- Runs as non-root user in container
- Network isolation recommended for production

## Migration from Whisper-WebUI

1. Deploy new container alongside existing
2. Update session manager to use new API endpoints
3. Test transcription accuracy and performance
4. Migrate model cache if needed
5. Remove old container

## Contributing

When improving this container:

1. **Test thoroughly**: Verify with different audio formats and models
2. **Update documentation**: Keep README in sync with code changes
3. **Performance**: Profile memory and CPU usage
4. **Security**: Review any new dependencies or network calls
5. **Compatibility**: Test with different GPU configurations
