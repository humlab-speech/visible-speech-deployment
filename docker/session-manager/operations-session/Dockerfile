# Stage 1: Base
FROM rocker/rstudio:4 AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="This image is used for background operations in the VISP system, such as creating the EmuDB database"

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
    apt-get install -y zlib1g-dev nano curl libxml2-dev libnode-dev r-cran-v8 libavfilter-dev libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev cmake  && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Miniconda installation, with Mamba
RUN curl -Lo miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
    sh miniconda.sh -b -p $HOME/miniconda && \
    rm miniconda.sh && \
    export PATH="$HOME/miniconda/bin:$PATH" && \
    conda install -y -c conda-forge mamba

ENV R_HOME="/root/miniconda/lib/R" \
    PATH="/root/miniconda/lib/R/bin:/root/miniconda/bin:$PATH"

# Install r-av package for R audio handling
RUN mamba install -y -c conda-forge r-av && \
    conda clean -a -y

WORKDIR /home/rstudio

# Stage 2: R packages
FROM r-base:4.3.3 AS r_packages
# NOTE THAT R VERSION SHOULD MATCH THE SAME AS THE BASE IMAGE

# In case the build fails, turn off format security to make it possible to compile some older C code. Consider the implications of this!
RUN echo $(R RHOME) && \
    sed -i 's/-Werror=format-security//g' $(R RHOME)/etc/Makeconf && \
    sed -i 's/-Werror=implicit-function-declaration//g' $(R RHOME)/etc/Makeconf

ENV CRAN_MIRROR="https://cran.uib.no/"

RUN rm /etc/localtime && \
    ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime && \
    apt-get update && \
    apt-get install -y git libcurl4-openssl-dev curl libxml2-dev libnode-dev r-cran-v8 libavfilter-dev libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev cmake  && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages(c('curl', 'git2r', 'openxlsx', 'gt', 'tidyverse', 'tidymodels', 'remotes'), repos='${CRAN_MIRROR}')"

RUN R -e "options(repos = c(CRAN = '${CRAN_MIRROR}'));  \
    remotes::install_github('IPS-LMU/emuR', dependencies = 'Imports'); \
    remotes::install_github('IPS-LMU/wrassp', dependencies = 'Imports');"
    # 2024-05-20 Removed these packages for now after discussion. 
    # remotes::install_github('tjmahr/tjm.praat'); \
    # remotes::install_github('humlab-speech/superassp', dependencies = 'Imports'); \
    # remotes::install_github('johanvonboer/eloquent.researcher', dependencies = 'Imports'); \
    # remotes::install_github('humlab-speech/reindeer', dependencies = 'Imports')"

# Stage 3: Final
FROM base AS final

# Copy the R packages from the R packages stage
# Verify that this command is correct by starting the target image and executing "Rscript -e '.libPaths()'" in the terminal
COPY --from=r_packages /usr/local/lib/R/site-library /root/miniconda/lib/R/library

# PRAAT installation
WORKDIR /    

# Depricated praat installation 2024-05-20
# COPY ./praat/praat.tgz /praat.tgz
# RUN tar xzf praat.tgz && \
#     mv praat_nogui /usr/bin/praat_nogui && \
#     ln -s /usr/bin/praat_nogui /usr/bin/praat && \
#     rm /praat.tgz

#Node.js installation using node version manager
ENV NVM_DIR="/root/.nvm"

RUN mkdir /node_install && \
    cd /node_install && \
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && \
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" && \
    nvm install 20 && nvm alias default 20 && \
    ln -s $(which node) /usr/local/bin/node && \
    ln -s $(which npm) /usr/local/bin/npm

WORKDIR /home/rstudio

ENV CRAN_MIRROR="https://cran.uib.no/"
# These packages are for some reason still missing from the R packages stage, and must be installed here.
# If we get R packages to install correctly in the base image, we can remove this stage.
RUN R -e "install.packages(c('jsonlite', 'stringi'), repos='${CRAN_MIRROR}')"

# Container agent install
ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache
WORKDIR /tmp
RUN git clone https://github.com/humlab-speech/container-agent && \
    cd container-agent && \
    npm install && \ 
    npm run build && \
    cp -R ./dist /container-agent

# Cleanup
RUN conda clean -a -y -q && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*

# Create a project directory and set permissions
RUN mkdir /home/rstudio/project && \
    chown rstudio:rstudio /home/rstudio/project && \
    chmod 755 /home/rstudio/project

WORKDIR /home/rstudio/project