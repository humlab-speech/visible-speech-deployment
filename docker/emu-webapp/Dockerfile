FROM debian:bullseye

RUN apt-get update && apt-get install -y curl

RUN curl -fsSL https://deb.nodesource.com/setup_16.x | bash -

RUN apt-get update && apt-get install -y nodejs git nano wget rubygems ruby-dev build-essential xsel python2

WORKDIR /app

RUN gem install sass

RUN gem install compass

#RUN git clone https://github.com/IPS-LMU/EMU-webApp.git

COPY ./EMU-webApp /app/EMU-webApp

WORKDIR /app/EMU-webApp

RUN npm install

CMD ["/usr/bin/npm", "run", "build-start"]

