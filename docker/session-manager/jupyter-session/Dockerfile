# Stage 1: Base
FROM quay.io/jupyter/datascience-notebook:r-4.3.3 AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The Jupyter session image, which is instantiated when a new Jupyter session is spawned in the VISP interface"

USER root

# Update the package list and change the package source to a Swedish mirror
RUN sed -i 's/archive.ubuntu.com/se.archive.ubuntu.com/g' /etc/apt/sources.list

WORKDIR /opt/conda/lib
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8.so ./ && \
    ln -s /usr/lib/x86_64-linux-gnu/libv8_libplatform.so ./

# Timezone fixes and base software installation
RUN rm /etc/localtime && \
    ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime && \
    apt-get update && \
    apt-get install -y ffmpeg zlib1g-dev nano curl nodejs libnode-dev r-cran-v8 libavfilter-dev cmake && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/jovyan

# Stage 2: Python packages
FROM base AS python_packages

RUN pip install --upgrade pip && \
    pip install --prefer-binary numpy \
                               torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cpu \
                               https://github.com/pyannote/pyannote-audio/archive/develop.zip \
                               speechbrain opensmile librosa torchcrepe pyworld amfm_decompy tensorflow tensorflow_hub \
                               json_tricks && \
    git clone --recursive https://github.com/r9y9/pyreaper && \
    pip install ./pyreaper/ && rm -rf pyreaper && \
    git clone --recursive https://github.com/r9y9/pysptk && \
    pip install ./pysptk/ && rm -rf pysptk


# Stage 4: Copy MATLAB runtime from Rstudio session image to a clean image
FROM python_packages AS matlab

# We copy the installation from the Rstudio session image, to avoid doing it twice
COPY --from=visp-rstudio-session /usr/local/MATLAB /usr/local/MATLAB

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

# Grab the compiled R from the visp_operations_session image, avoiding building them twice
# NOTE: If the operations session image is updated, this image must be rebuilt
COPY --from=visp-operations-session /usr/local/lib/R/site-library /opt/conda/lib/R/library

# ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache
WORKDIR /tmp
# Move .container-agent to /container-agent:
ADD --chown=root:root ./files/container-agent /container-agent-install
RUN cd /container-agent-install && \
    npm install && \ 
    npm run build && \
    cp -R ./dist /container-agent

COPY ./files/jupyter_notebook_config.py /home/jovyan/.jupyter/jupyter_notebook_config.py

# Cleanup
RUN conda clean -a -y -q && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*    

RUN mkdir /home/jovyan/project && \
    chown -R jovyan:users /home/jovyan/project && \
    chmod -R 755 /home/jovyan/project

WORKDIR /home/jovyan/project
