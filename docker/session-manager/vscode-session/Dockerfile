FROM linuxserver/code-server:version-v3.11.1

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The vscode session image, which is instantiated when a new vscode session is spawned in the VISP interface"

#USER root

#RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update && apt-get install -y curl

#Get nodejs so we can run the git-agent
RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
RUN apt-get update

RUN apt-get install -y zlib1g-dev nano docker.io r-cran-git2r libgit2-26 nodejs libcurl4-openssl-dev libxml2-dev

RUN echo 'install.packages("emuR")' | R --save
RUN echo 'install.packages("openxlsx")' | R --save
RUN echo 'install.packages("ggpubr")' | R --save
RUN echo 'install.packages("gt")' | R --save
RUN echo 'install.packages("tidyverse")' | R --save
RUN echo 'install.packages("tidymodels")' | R --save
RUN echo 'install.packages("devtools")' | R --save
RUN echo 'install.packages("rPraat")' | R --save

#RUN echo 'library(devtools); install_github("IPS-LMU/wrassp",dependencies = "Imports")' | R --save
#RUN echo 'library(devtools); install_github("tjmahr/tjm.praat")' | R --save
#RUN echo 'library(devtools); install_github("humlab-speech/superassp",dependencies = "Imports")' | R --save
#RUN echo 'devtools::install_github("johanvonboer/eloquent.researcher",dependencies = "Imports")' | R --save

#Always force the next cmds, stolen from https://stackoverflow.com/questions/35134713/disable-cache-for-specific-run-commands
ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache

#RUN echo 'library(devtools); install_github("humlab-speech/reindeer", dependencies = TRUE)' | R --save

#RUN curl --output praat.tgz https://www.fon.hum.uva.nl/praat/praat6152_linux64nogui.tar.gz && tar xzf praat.tgz && mv praat_nogui /usr/bin/praat_nogui && ln -s /usr/bin/praat_nogui /usr/bin/praat

COPY ./container-agent /container-agent

