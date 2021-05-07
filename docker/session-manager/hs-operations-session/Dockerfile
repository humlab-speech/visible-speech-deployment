FROM rocker/rstudio:4.0.5

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y zlib1g-dev nano r-cran-git2r curl nodejs

RUN echo 'install.packages("emuR")' | R

RUN echo 'install.packages("packrat")' | R

COPY ./scripts /scripts
