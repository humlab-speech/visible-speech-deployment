FROM debian:bookworm

RUN apt-get update && apt-get install -y curl

RUN apt-get update && apt-get install -y nodejs git nano wget procps

RUN useradd -m visp

WORKDIR /app

USER visp

CMD ["/usr/bin/npm", "run", "start"]

