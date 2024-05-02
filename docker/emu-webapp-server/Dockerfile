FROM debian:bookworm

#Nodejs installation
RUN apt-get update && apt-get install -y curl ca-certificates gnupg
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=20
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list

RUN apt-get update && apt-get install -y nodejs git nano wget procps

RUN useradd -m visp

WORKDIR /home/visp

USER visp

#anti-cache line
#ADD "https://api.github.com/repos/humlab-speech/emu-webapp-server/commits?per_page=1" latest_commit

RUN git clone https://github.com/humlab-speech/emu-webapp-server
RUN mv emu-webapp-server app
WORKDIR /home/visp/app
RUN npm install

CMD ["node", "/home/visp/app/src/main.js"]
