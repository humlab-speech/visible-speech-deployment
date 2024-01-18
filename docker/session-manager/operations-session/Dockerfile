
# Stage 1: Base
FROM rocker/rstudio:4 AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="This image is used for background operations in the VISP system, such as creating the EmuDB database"

USER root

RUN sed -i 's/archive.ubuntu.com/se.archive.ubuntu.com/g' /etc/apt/sources.list

#Nodejs installation
RUN apt-get update && apt-get install -y curl ca-certificates gnupg
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=20
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
RUN apt-get update
RUN apt-get install nodejs -y

COPY Miniconda3-latest-Linux-x86_64.sh ./
RUN bash ./Miniconda3-latest-Linux-x86_64.sh -b
RUN /root/miniconda3/bin/conda install mamba -n base -c conda-forge

RUN rm /etc/localtime && \
    ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime && \
    apt-get update && \
    apt-get install -y zlib1g-dev nano curl r-cran-v8 libavfilter-dev libfontconfig1-dev libxml2-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev cmake python3-pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    /root/miniconda3/bin/mamba install -y -c conda-forge r-av && \
    /root/miniconda3/bin/conda clean -a -y

WORKDIR /opt/conda/lib
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8.so ./ && \
    ln -s /usr/lib/x86_64-linux-gnu/libv8_libplatform.so ./

WORKDIR /home/rstudio

# Stage 2: R packages
FROM base AS r_packages

RUN echo 'chooseCRANmirror(graphics=FALSE, ind=62);install.packages(c("git2r", "openxlsx", "ggpubr", "gt", "tidyverse", "tidymodels", "devtools"), Ncpus = 6)' | R --save && \
    echo 'devtools::install_github("IPS-LMU/emuR",dependencies = "Imports")' | R --save && \
    echo 'devtools::install_github("IPS-LMU/wrassp",dependencies = "Imports")' | R --save && \
    echo 'devtools::install_github("tjmahr/tjm.praat")' | R --save && \
    echo 'devtools::install_github("humlab-speech/superassp",dependencies = "Imports")' | R --save && \
    echo 'devtools::install_github("humlab-speech/reindeer", dependencies = TRUE)' | R --save && \
    echo 'devtools::install_github("johanvonboer/eloquent.researcher",dependencies = "Imports")' | R --save

# Stage 3: Python packages
FROM r_packages AS python_packages

RUN pip install --upgrade pip && \
    pip install --prefer-binary numpy && \
    pip install --prefer-binary torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cpu && \
    pip install --prefer-binary https://github.com/pyannote/pyannote-audio/archive/develop.zip && \
    pip install --prefer-binary speechbrain opensmile librosa torchcrepe pyworld amfm_decompy tensorflow tensorflow_hub && \
    git clone --recursive https://github.com/r9y9/pyreaper && \
    pip install ./pyreaper/ && rm -rf pyreaper && \
    git clone --recursive https://github.com/r9y9/pysptk && \
    pip install ./pysptk/ && rm -rf pysptk

# Stage 4: Matlab
FROM python_packages AS matlab

RUN mkdir /matlab_install

# Copy the local matlab_runtime_install.zip file to the Docker image
COPY ./matlab_runtime_installer/current_matlab_install /matlab_install/matlab_runtime_install.zip

RUN export MATLAB_RUNTIME_DIRECTORY=/usr/local/MATLAB/MATLAB_Runtime/v913/ && \
    unzip /matlab_install/matlab_runtime_install.zip -d /matlab_install/ && \
    rm /matlab_install/matlab_runtime_install.zip && \
    /matlab_install/install -mode silent -agreeToLicense yes && \
    rm -rf /matlab_install

RUN mkdir /matlab-scripts
# Copy the matlab scripts 
COPY ./matlab-scripts/*.zip /matlab-scripts/	
COPY ./matlab-scripts/*.sh /matlab-scripts/
WORKDIR /matlab-scripts
RUN chmod +x unzip_and_setup.sh && \
    chmod +x configure_matlab_env.sh && \
    ./configure_matlab_env.sh set

# NOTE: The unzip_and_setup scripts creates the setenv script automatically.
RUN /matlab-scripts/unzip_and_setup.sh /matlab-scripts/
RUN /bin/bash -c "source /matlab-scripts/setenv.sh"

WORKDIR /home/rstudio

# Stage 5: Final
FROM matlab AS final

RUN echo 'devtools::install_github("humlab-speech/reindeer", dependencies = TRUE)' | R --save

WORKDIR /

COPY ./praat/praat.tgz /praat.tgz
RUN tar xzf praat.tgz && \
    mv praat_nogui /usr/bin/praat_nogui && \
    ln -s /usr/bin/praat_nogui /usr/bin/praat


WORKDIR /home/rstudio
RUN git clone https://github.com/humlab-speech/container-agent
RUN cd container-agent && npm install && npm run build && cp -R ./dist /container-agent

#COPY ./container-agent /container-agent
COPY ./files/jupyter_notebook_config.py /home/rstudio/.jupyter/jupyter_notebook_config.py

RUN /root/miniconda3/bin/conda clean -a -y -q

RUN mkdir /home/rstudio/project
