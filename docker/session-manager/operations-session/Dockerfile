FROM rocker/rstudio:4.0.5

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="This image is used for background operations in the VISP system, such as creating the EmuDB database and pushing it to GitLab"

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update && apt-get install -y curl

RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y zlib1g-dev nano r-cran-git2r nodejs r-cran-jsonlite r-cran-base64enc

RUN echo 'install.packages("devtools")' | R

RUN echo 'library(devtools); install_github("IPS-LMU/emuR")' | R
RUN echo 'library(devtools); install_github("tjmahr/tjm.praat")' | R
RUN echo 'library(devtools); install_github("humlab-speech/superassp",dependencies = "Imports")' | R

#Always force the next COPY cmd, stolen from https://stackoverflow.com/questions/35134713/disable-cache-for-specific-run-commands
ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache

COPY ./container-agent /container-agent