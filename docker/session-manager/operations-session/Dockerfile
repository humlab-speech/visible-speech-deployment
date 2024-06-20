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
    apt-get install -y rsync ffmpeg zlib1g-dev nano curl libxml2-dev libnode-dev r-cran-v8 libavfilter-dev libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev cmake  && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/rstudio

# Stage 2: R packages
FROM base AS r_packages
# NOTE THAT R VERSION SHOULD MATCH THE SAME AS THE BASE IMAGE

# In case the build fails, turn off format security to make it possible to compile some older C code. Consider the implications of this!
RUN echo $(R RHOME) && \
    sed -i 's/-Werror=format-security//g' $(R RHOME)/etc/Makeconf && \
    sed -i 's/-Werror=implicit-function-declaration//g' $(R RHOME)/etc/Makeconf

ENV CRAN_MIRROR="https://cran.uib.no/"
ENV PKG_CRAN_MIRROR=${CRAN_MIRROR}
RUN R -e "install.packages(c('pak'), repos='${CRAN_MIRROR}')"
RUN R -e "options(repos = c(CRAN = '${CRAN_MIRROR}')); \
    pak::pkg_install(c('curl', 'git2r', 'openxlsx', 'gt', 'tidyverse', 'tidymodels', 'remotes'))"
RUN R -e "options(repos = c(CRAN = '${CRAN_MIRROR}')); \
    pak::pkg_install(c('IPS-LMU/emuR', 'IPS-LMU/wrassp', 'jsonlite','stringi','av'));"

# Stage 3: Final
FROM r_packages AS final

WORKDIR /    

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

# Container agent install
# ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache
WORKDIR /tmp
# Move .container-agent to /container-agent:
ADD --chown=root:root ./files/container-agent /container-agent-install
RUN cd /container-agent-install && \
    npm install && \ 
    npm run build && \
    cp -R ./dist /container-agent && \
    rm -rf /container-agent-install

# Cleanup
RUN apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/*

# Create a project directory and set permissions
RUN mkdir /home/rstudio/project && \
    chown rstudio:rstudio /home/rstudio/project && \
    chmod 755 /home/rstudio/project

WORKDIR /home/rstudio/project