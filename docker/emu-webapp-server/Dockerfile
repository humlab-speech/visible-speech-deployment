FROM node:23-bookworm-slim

#Nodejs installation
RUN apt-get update && apt-get install -y curl git nano wget procps

RUN useradd -m visp

WORKDIR /home/visp

USER visp

RUN git clone https://github.com/humlab-speech/emu-webapp-server
RUN mv emu-webapp-server app
WORKDIR /home/visp/app
RUN npm install
RUN mkdir logs
CMD ["node", "/home/visp/app/src/main.js"]
