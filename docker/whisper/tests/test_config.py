import functools

from modules.utils.paths import *
from modules.utils.youtube_manager import *

import os
import torch

TEST_FILE_DOWNLOAD_URL = "https://github.com/jhj0517/whisper_flutter_new/raw/main/example/assets/jfk.wav"
TEST_FILE_PATH = os.path.join(WEBUI_DIR, "tests", "jfk.wav")
TEST_YOUTUBE_URL = "https://www.youtube.com/watch?v=4WEQtgnBu0I&ab_channel=AndriaFitzer"
TEST_WHISPER_MODEL = "tiny.en"
TEST_UVR_MODEL = "UVR-MDX-NET-Inst_HQ_4"
TEST_NLLB_MODEL = "facebook/nllb-200-distilled-600M"
TEST_SUBTITLE_SRT_PATH = os.path.join(WEBUI_DIR, "tests", "test_srt.srt")
TEST_SUBTITLE_VTT_PATH = os.path.join(WEBUI_DIR, "tests", "test_vtt.vtt")


@functools.lru_cache
def is_cuda_available():
    return torch.cuda.is_available()


@functools.lru_cache
def is_pytube_detected_bot(url: str = TEST_YOUTUBE_URL):
    try:
        yt_temp_path = os.path.join("modules", "yt_tmp.wav")
        if os.path.exists(yt_temp_path):
            return False
        yt = get_ytdata(url)
        audio = get_ytaudio(yt)
        return False
    except Exception as e:
        print(f"Pytube has detected as a bot: {e}")
        return True

