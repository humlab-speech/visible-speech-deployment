FROM debian:buster

RUN apt-get update && apt-get install -y curl

RUN curl -sL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get update && apt-get install -y nodejs git nano wget rubygems ruby-dev build-essential xsel

WORKDIR /app

RUN gem install sass

RUN gem install compass

#RUN git clone https://github.com/IPS-LMU/EMU-webApp.git

#Temporarily using the gitlab_path branch of the forked repo
RUN git clone https://github.com/humlab-speech/EMU-webApp

WORKDIR /app/EMU-webApp

RUN git checkout gitlab_path

RUN npm install

CMD ["/usr/bin/npm", "run", "build-start"]

