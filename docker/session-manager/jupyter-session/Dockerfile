FROM jupyter/datascience-notebook:r-4.0.3

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The Jupyter session image, which is instantiated when a new Jupyter session is spawned in the VISP interface"

USER root

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update
RUN apt-get install -y zlib1g-dev nano curl libnode64 libnode-dev r-cran-v8

#Not sure why I have to manually link in libv8 like this, but ld fails otherwise when trying to build V8 (which is a dep of emuR)
WORKDIR /opt/conda/lib
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8.so ./
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8_libplatform.so ./

WORKDIR /home/jovyan

RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("git2r")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("openxlsx")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("ggpubr")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("gt")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("tidyverse")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("tidymodels")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("devtools")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("emuR")' | R --save

RUN echo 'devtools::install_github("tjmahr/tjm.praat")' | R --save
RUN echo 'devtools::install_github("humlab-speech/superassp",dependencies = "Imports")' | R --save
RUN echo 'devtools::install_github("humlab-speech/reindeer",dependencies = "Imports")' | R --save
#RUN echo 'devtools::install_github("humlab-speech/eloquent.researcher",dependencies = "Imports")' | R --save
RUN echo 'devtools::install_github("johanvonboer/eloquent.researcher",dependencies = "Imports")' | R --save

#Always force the next COPY cmd, stolen from https://stackoverflow.com/questions/35134713/disable-cache-for-specific-run-commands
ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache

COPY ./container-agent /container-agent
COPY ./files/jupyter_notebook_config.py /home/jovyan/.jupyter/jupyter_notebook_config.py

RUN mkdir /home/jovyan/project
