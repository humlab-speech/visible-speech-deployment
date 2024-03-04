FROM debian:bullseye

RUN apt-get update && apt-get install -y curl

#Nodejs installation
RUN apt-get update && apt-get install -y curl ca-certificates gnupg
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=16
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list

RUN apt-get update && apt-get install -y nodejs git nano wget rubygems ruby-dev build-essential xsel python2 nodejs

WORKDIR /app

RUN gem install sass

RUN gem install compass

RUN git clone https://github.com/humlab-speech/EMU-webApp

WORKDIR /app/EMU-webApp

RUN npm install

CMD ["/usr/bin/npm", "run", "build-start"]

