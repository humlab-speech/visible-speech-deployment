# Stage 1: Build container-agent (no host npm needed!)
FROM docker.io/node:20.20.0-alpine3.22 AS container_agent_builder
WORKDIR /build
COPY ./container-agent /build/
RUN npm install && \
    npm run build

# Stage 2: Base
FROM quay.io/jupyter/datascience-notebook:r-4.5.2 AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="Jupyter + R session image. Used for interactive Jupyter sessions AND short-lived operations tasks (EMU-DB creation, audio import, etc.)"

USER root

WORKDIR /opt/conda/lib
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8.so ./ && \
    ln -s /usr/lib/x86_64-linux-gnu/libv8_libplatform.so ./

# Timezone fixes and base software installation
RUN rm /etc/localtime && \
    ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime && \
    apt update && apt upgrade -y && \
    apt install -y ffmpeg zlib1g-dev nano npm curl nodejs libnode-dev r-cran-v8 \
                   libavfilter-dev cmake socat rsync \
                   libxml2-dev libfontconfig1-dev libharfbuzz-dev libfribidi-dev \
                   libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev \
                   libcurl4-openssl-dev && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/jovyan

# Stage 3: R packages (emuR + dependencies for container-agent R scripts)
FROM base AS r_packages

# Relax compiler flags so older C code can compile
RUN RHOME=$(R RHOME) && \
    sed -i 's/-Werror=format-security//g' $RHOME/etc/Makeconf && \
    sed -i 's/-Werror=implicit-function-declaration//g' $RHOME/etc/Makeconf

ENV CRAN_MIRROR="https://cran.uib.no/"

RUN R -e "install.packages('pak', repos='${CRAN_MIRROR}'); \
          if (!requireNamespace('pak', quietly = TRUE)) stop('pak installation failed')"

RUN R -e "options(repos = c(CRAN = '${CRAN_MIRROR}')); \
    pak::pkg_install(c('curl', 'git2r', 'openxlsx', 'gt', 'tidyverse', 'tidymodels', 'remotes'))"
RUN R -e "options(repos = c(CRAN = '${CRAN_MIRROR}')); \
    pak::pkg_install(c('IPS-LMU/emuR', 'IPS-LMU/wrassp', 'jsonlite', 'stringi', 'av'));"

# Stage 4: Final
FROM r_packages AS final

# Copy the pre-built container-agent from builder stage
COPY --from=container_agent_builder /build/dist /container-agent

COPY ./files/jupyter_notebook_config.py /home/jovyan/.jupyter/jupyter_notebook_config.py

# Cleanup
RUN conda clean -a -y -q && \
    apt clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*

RUN mkdir -p /home/jovyan/project && \
    chown -R jovyan:users /home/jovyan/project && \
    chmod -R 755 /home/jovyan/project

WORKDIR /home/jovyan/project

# UDS isolation wrapper: starts socat proxy bridge before Jupyter
COPY ./files/jupyter-uds-wrapper.sh /usr/local/bin/jupyter-uds-wrapper.sh
RUN chmod +x /usr/local/bin/jupyter-uds-wrapper.sh

# Ensure Jupyter runtime and local dirs exist and are owned
# This prevents permission errors when the notebook server
# tries to create /home/jovyan/.local/share/jupyter/runtime
RUN mkdir -p /home/jovyan/.local/share/jupyter/runtime && \
    chown -R jovyan:users /home/jovyan/.local && \
    chmod -R 700 /home/jovyan/.local

# Switch back to jovyan user
USER jovyan
