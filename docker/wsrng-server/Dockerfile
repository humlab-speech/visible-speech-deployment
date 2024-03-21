FROM node:lts-buster

RUN apt-get install -y git curl

WORKDIR /

RUN git clone https://github.com/humlab-speech/wsrng-server

WORKDIR /wsrng-server

RUN npm install

CMD ["/usr/local/bin/node", "./src/main.js"]
