FROM rocker/rstudio:4.0.5

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="This image is used for background operations in the humlab speech system, such as creating the EmuDB database and pushing it to GitLab"

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y zlib1g-dev nano r-cran-git2r curl nodejs

RUN echo 'install.packages("devtools")' | R

RUN echo 'library(devtools); install_github("IPS-LMU/emuR")' | R

COPY ./scripts /scripts
