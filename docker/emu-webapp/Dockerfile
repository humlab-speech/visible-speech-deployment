FROM debian:buster

RUN apt-get update && apt-get install -y curl

RUN curl -sL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y nodejs git nano wget rubygems ruby-dev build-essential xsel python2

WORKDIR /app

RUN gem install sass

RUN gem install compass

RUN git clone https://github.com/IPS-LMU/EMU-webApp.git

WORKDIR /app/EMU-webApp

RUN npm install

CMD ["/usr/bin/npm", "run", "build-start"]

