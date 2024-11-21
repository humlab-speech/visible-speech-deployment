FROM node:23-bookworm

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update && apt-get install -y \
    apt-transport-https \
    gnupg-agent \
    software-properties-common \
    zlib1g-dev nano r-cran-git2r libgit2-dev docker.io

WORKDIR /
RUN git clone https://github.com/humlab-speech/session-manager
WORKDIR /session-manager
RUN npm install

ENV GIT_SSL_NO_VERIFY=true
ENV GIT_DISCOVERY_ACROSS_FILESYSTEM=true

CMD ["node", "/session-manager/src/index.js"]
