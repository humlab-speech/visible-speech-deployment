FROM rocker/rstudio:4

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="This image is used for background operations in the VISP system, such as creating the EmuDB database and pushing it to GitLab"

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

#Switch to using swedish ubuntu archive, since it sometimes fails to download from the universal archive, for unknown reasons
RUN sed -i 's/archive.ubuntu.com/se.archive.ubuntu.com/g' /etc/apt/sources.list

RUN apt-get update && apt-get install -y curl ca-certificates gnupg

#Nodejs installation
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=20
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
RUN apt-get update
RUN apt-get install nodejs -y

RUN apt-get update
RUN apt-get install -y zlib1g-dev nano nodejs
RUN apt-get install -y r-cran-git2r
RUN apt-get install -y r-cran-jsonlite
RUN apt-get install -y r-cran-base64enc

RUN echo 'install.packages("devtools")' | R
RUN echo 'library(devtools); install_github("IPS-LMU/wrassp",dependencies = "Imports")' | R
RUN echo 'library(devtools); install_github("tjmahr/tjm.praat")' | R

#libavfilter-dev is needed for superassp
RUN apt-get install -y libavfilter-dev
RUN echo 'library(devtools); install_github("humlab-speech/superassp",dependencies = "Imports")' | R
RUN echo 'library(devtools); install_github("IPS-LMU/emuR")' | R
RUN echo 'library(devtools); install_github("humlab-speech/reindeer", dependencies = TRUE)' | R

ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache

COPY ./praat/praat.tgz /praat.tgz
RUN tar xzf praat.tgz && mv praat_nogui /usr/bin/praat_nogui && ln -s /usr/bin/praat_nogui /usr/bin/praat

COPY ./operations-session/wavs /tmp/wavs
COPY ./container-agent /container-agent
