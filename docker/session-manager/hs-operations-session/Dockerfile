FROM rocker/rstudio:4.0.5

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN echo 'install.packages("emuR")' | R

RUN apt-get update && apt-get install -y zlib1g-dev nano r-cran-git2r curl

#Get nodejs so we can run the git-agent
RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
RUN apt-get install -y nodejs

COPY ./scripts /scripts
