FROM debian:bullseye
 
RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime
 
RUN sed -i 's/archive.ubuntu.com/se.archive.ubuntu.com/g' /etc/apt/sources.list
 
#Nodejs installation
RUN apt-get update && apt-get install -y curl ca-certificates gnupg
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=20
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
 
RUN apt-get update && apt-get install -y \
    apt-transport-https \
    gnupg-agent \
    software-properties-common \
    zlib1g-dev nano r-cran-git2r libgit2-1.1 libgit2-dev nodejs docker.io ffmpeg python3 pip
 
WORKDIR /
RUN git clone https://github.com/humlab-speech/session-manager
WORKDIR /session-manager
RUN npm install
 
ENV GIT_SSL_NO_VERIFY=true
ENV GIT_DISCOVERY_ACROSS_FILESYSTEM=true
 
WORKDIR /
RUN git clone https://github.com/humlab-speech/whisper-script
WORKDIR /whisper-script
RUN pip install -r requirements.txt
 
WORKDIR /session-manager
 
CMD ["node", "/session-manager/src/index.js"]
