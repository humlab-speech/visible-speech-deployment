FROM jupyter/r-notebook:latest

USER root

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

#zlib is needed to build the R git2r lib, which is a dependency of emuR
RUN apt-get update && apt-get install -y zlib1g-dev nano

#RUN R -e 'chooseCRANmirror(graphics=FALSE, ind=61);'

RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("emuR")' | R --save

RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("git2r")' | R --save

RUN apt-get install -y docker.io

COPY ./rscripts /rscripts

