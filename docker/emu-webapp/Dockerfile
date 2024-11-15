FROM node:22-bookworm

# Install non-npm related packages
RUN apt-get update && \
    apt-get install -y curl ca-certificates gnupg git nano wget rubygems ruby-dev build-essential xsel

WORKDIR /app

RUN gem install sass

RUN gem install compass

RUN git clone https://github.com/humlab-speech/EMU-webApp

WORKDIR /app/EMU-webApp

RUN npm install --legacy-peer-deps

CMD ["/usr/local/bin/npm", "run", "build-start"]