FROM node:lts-bookworm

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update && apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg-agent \
    software-properties-common \
    r-base r-base-dev

#RUN curl -fsSL https://download.docker.com/linux/debian/gpg | apt-key add -

#RUN add-apt-repository \
#   "deb [arch=amd64] https://download.docker.com/linux/debian \
#   $(lsb_release -cs) \
#   stable"

#RUN apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io
RUN apt-get update && apt-get install -y docker.io

WORKDIR /session-manager

ENV GIT_SSL_NO_VERIFY=true
ENV GIT_DISCOVERY_ACROSS_FILESYSTEM=true

CMD ["node", "/session-manager/src/index.js"]
