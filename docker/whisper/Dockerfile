FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y curl git python3 python3-pip python3-venv && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* && \
    mkdir -p /Whisper-WebUI

WORKDIR /

WORKDIR /Whisper-WebUI
RUN git clone https://github.com/jhj0517/Whisper-WebUI . && \
    git checkout 57ccf05
# Pinned to commit 57ccf05: "Merge pull request #610 from jhj0517/fix/torchaudio-lockdown"
# This version includes the updated Gradio API with include_subdirectory and save_same_dir parameters

RUN python3 -m venv venv && \
    . venv/bin/activate && \
    pip install --no-cache-dir torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121 && \
    pip install --no-cache-dir -r requirements.txt

ENV PATH="/Whisper-WebUI/venv/bin:$PATH"
ENV LD_LIBRARY_PATH=/Whisper-WebUI/venv/lib64/python3.11/site-packages/nvidia/cublas/lib:/Whisper-WebUI/venv/lib64/python3.11/site-packages/nvidia/cudnn/lib

CMD [ "python3", "app.py", "--server_name", "0.0.0.0", "--server_port", "7860" ]
