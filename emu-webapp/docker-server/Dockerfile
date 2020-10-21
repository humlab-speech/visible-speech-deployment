FROM debian:buster

RUN apt-get update && apt-get install -y curl

RUN curl -sL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y nodejs git nano wget build-essential xsel

WORKDIR /app

RUN git clone https://github.com/IPS-LMU/EMU-webApp.git

WORKDIR /app/EMU-webApp

RUN npm install

WORKDIR /app/EMU-webApp/exampleServers

CMD ["node", "nodeEmuProtocolWsServer.js"]
