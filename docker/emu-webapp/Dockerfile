FROM debian:bullseye

RUN apt-get update && apt-get install -y curl

RUN curl -sL https://deb.nodesource.com/setup_16.x | bash -

#Switch to this version of node/npm instead? To avoid build errors about unsupported lockfile version. As of the time of this writing this is v16.x
#RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -

RUN apt-get update && apt-get install -y nodejs git nano wget rubygems ruby-dev build-essential xsel python2

WORKDIR /app

RUN gem install sass

RUN gem install compass

RUN git clone https://github.com/IPS-LMU/EMU-webApp.git

WORKDIR /app/EMU-webApp

RUN npm install

CMD ["/usr/bin/npm", "run", "build-start"]

