# Stage 1: Base
FROM visp-operations-session AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The Rstudio session image, which is instantiated when a new Rstudio session is spawned in the VISP interface"

USER root

# Miniconda installation, with Mamba
WORKDIR /root

RUN curl -Lo miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
RUN sh miniconda.sh -b -p /opt/miniconda
RUN rm miniconda.sh
RUN /opt/miniconda/bin/conda install -y -c conda-forge mamba


#RUN curl -Lo miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
#    sh miniconda.sh -b -p /opt/miniconda && \
#    rm miniconda.sh && \
#    /opt/miniconda/bin/conda install -y -c conda-forge mamba

ENV PATH="/opt/miniconda/bin:$PATH"

# Stage 2: Python packages
FROM base AS python_packages

RUN pip install --upgrade pip && \
    pip install --prefer-binary numpy \
                               torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cpu \
                               pyannote.audio \
                               speechbrain opensmile librosa torchcrepe pyworld amfm_decompy tensorflow tensorflow_hub \
                               json_tricks && \
    cd /tmp && \
    git clone --recursive https://github.com/r9y9/pyreaper && \
    pip install ./pyreaper/ && rm -rf pyreaper && \
    git clone --recursive https://github.com/r9y9/pysptk && \
    pip install ./pysptk/ && rm -rf pysptk && \
    cd / && \
    rm -rf /tmp/*

# Stage 3: Final
FROM python_packages AS final

# Cleanup
RUN conda clean -a -y -q && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*

WORKDIR /home/rstudio/project
