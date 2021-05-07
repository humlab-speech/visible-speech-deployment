FROM rocker/rstudio:4.0.5

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

#RUN apt-get update

#zlib is needed to build the R git2r lib, which is a dependency of emuR
#RUN apt-get install -y zlib1g-dev nano curl

#RUN apt-get install -y docker.io r-cran-git2r libgit2-28

#Get nodejs so we can run the git-agent
RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
RUN apt-get update

#RUN apt-get install -y nodejs


RUN apt-get install -y zlib1g-dev nano curl docker.io r-cran-git2r libgit2-28 nodejs



#RUN apt-get install -y docker.io r-cran-git2r libgit2-28 r-cran-packrat

#Get nodejs so we can run the git-agent
#RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
#RUN apt-get install -y nodejs

#RUN echo 'install.packages("emuR")' | R

RUN echo 'install.packages("packrat")' | R

COPY ./scripts /scripts

