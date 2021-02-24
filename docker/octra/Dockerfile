FROM nginx:1.19

RUN apt-get update && apt-get -y install nginx curl

WORKDIR /

RUN curl -OL https://github.com/IPS-LMU/octra/archive/v1.4.2.tar.gz

RUN tar xzf v1.4.2.tar.gz

WORKDIR /octra-1.4.2

RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -

RUN apt-get update && apt-get -y install nodejs

RUN npm install

RUN cp ./src/config/appconfig_sample.json src/config/appconfig.json

RUN npm run build

RUN cp -R ./dist/octra/* /usr/share/nginx/html/

