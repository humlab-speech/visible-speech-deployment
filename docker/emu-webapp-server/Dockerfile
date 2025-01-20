FROM node:23-bookworm-slim

#Nodejs installation
RUN apt-get update && apt-get install -y curl git nano wget procps

WORKDIR /home/node
USER node

RUN git clone https://github.com/humlab-speech/emu-webapp-server
RUN mv emu-webapp-server app
WORKDIR /home/node/app
RUN npm install
RUN mkdir logs
CMD ["node", "/home/node/app/src/main.js"]
