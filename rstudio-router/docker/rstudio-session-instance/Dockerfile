FROM rocker/rstudio:3.6.3

#zlib is needed to build the R git2r lib, which is a dependency of emuR
RUN apt-get update && apt-get install -y zlib1g-dev nano

RUN echo 'install.packages("emuR")' | R

RUN echo 'install.packages("git2r")' | R

RUN apt-get install -y docker.io
