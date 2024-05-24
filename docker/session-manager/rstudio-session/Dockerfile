# Stage 1: Base
FROM visp-operations-session AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The Rstudio session image, which is instantiated when a new Rstudio session is spawned in the VISP interface"

USER root

# Miniconda installation, with Mamba
WORKDIR /root
RUN curl -Lo miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
    sh miniconda.sh -b -p /opt/miniconda && \
    rm miniconda.sh && \
    /opt/miniconda/bin/conda install -y -c conda-forge mamba
   
ENV PATH="/opt/miniconda/bin:$PATH"    

# Stage 2: Python packages
FROM base AS python_packages

RUN pip install --upgrade pip && \
    pip install --prefer-binary numpy \
                               torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cpu \
                               https://github.com/pyannote/pyannote-audio/archive/develop.zip \
                               speechbrain opensmile librosa torchcrepe pyworld amfm_decompy tensorflow tensorflow_hub \
                               json_tricks && \
    cd /tmp && \
    git clone --recursive https://github.com/r9y9/pyreaper && \
    pip install ./pyreaper/ && rm -rf pyreaper && \
    git clone --recursive https://github.com/r9y9/pysptk && \
    pip install ./pysptk/ && rm -rf pysptk && \
    cd / && \
    rm -rf /tmp/*

# Stage 3: Matlab
FROM base AS matlab_builder

WORKDIR /matlab_install

# Copy the local matlab_runtime_install.zip file to the Docker image
COPY ./matlab_runtime_installer/current_matlab_install /matlab_install/matlab_runtime_install.zip

RUN apt-get update && apt-get install -y unzip && \
    unzip /matlab_install/matlab_runtime_install.zip -d /matlab_install/ && \
    rm /matlab_install/matlab_runtime_install.zip && \
    /matlab_install/install -mode silent -agreeToLicense yes

# Stage 4: Copy MATLAB runtime to a clean image
FROM python_packages AS matlab

COPY --from=matlab_builder /usr/local/MATLAB /usr/local/MATLAB

# Copy the matlab scripts
RUN mkdir /matlab-scripts
COPY ./matlab-scripts/*.zip ./matlab-scripts/*.sh /matlab-scripts/
WORKDIR /matlab-scripts

# Note: The configure matlab env script will set variables for the jovyan user.
# This will hopefully persist after the container has been created!
RUN chmod +x unzip_and_setup.sh && \
    chmod +x configure_matlab_env.sh && \
    ./configure_matlab_env.sh set
# Add python_wrappers subdirectory to system path
ENV PATH="/matlab-scripts/python_wrappers:${PATH}"

# NOTE: The unzip_and_setup scripts creates the setenv script automatically.
RUN /matlab-scripts/unzip_and_setup.sh /matlab-scripts/

WORKDIR /home/jovyan

# Stage 5: Final
FROM matlab AS final

# Cleanup
RUN conda clean -a -y -q && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*    

WORKDIR /home/rstudio/project