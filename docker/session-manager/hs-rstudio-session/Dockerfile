FROM rocker/rstudio:4.0.5

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The RStudio session image, which is instantiated when a new RStudio session is spawned in the humlab speech interface"

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

#Get nodejs so we can run the git-agent
RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
RUN apt-get update

RUN apt-get install -y zlib1g-dev nano curl docker.io r-cran-git2r libgit2-28 nodejs

RUN echo 'install.packages("emuR")' | R
RUN echo 'install.packages("openxlsx")' | R
RUN echo 'install.packages("ggpubr")' | R
RUN echo 'install.packages("gt")' | R
RUN echo 'install.packages("tidyverse")' | R
RUN echo 'install.packages("tidymodels")' | R
RUN echo 'install.packages("devtools")' | R
RUN echo 'install.packages("rPraat")' | R
#RUN echo 'library(devtools); install_github("humlab-speech/superassp")' | R
#RUN echo 'library(devtools); install_github("humlab-speech/reindeer")' | R

#IN THE FUTURE:
#RUN echo 'install.packages("reindeer")' | R
#RUN echo 'install.packages("superassp")' | R

#MAYBE AT SOME POINT ONE OF THESE:
#RUN echo 'install.packages("packrat")' | R
#RUN echo 'install.packages("renv")' | R

COPY ./files/.Rprofile /home/rstudio/.Rprofile

COPY ./scripts /scripts

