FROM debian:bookworm

ENV WEBCLIENT_BUILD=visp-build

RUN echo "WEBCLIENT_BUILD is set to ${WEBCLIENT_BUILD}"

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="A debian based installation of apache with shibboleth, acting as the VISP webserver, router and authentication layer"

#add nodejs repo
RUN apt-get update
RUN apt-get install -y ca-certificates curl gnupg iputils-ping
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=22
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list

#openssl is only needed for shib-keygen, and php-cli is just to ease with debugging
RUN apt-get update && apt-get install git nodejs apache2 libapache2-mod-shib libapache2-mod-php ntp openssl php-cli libapache2-mod-php nano curl php-mbstring php-mongodb php-zip php-xml php-pgsql --no-install-recommends -y

RUN a2enmod proxy proxy_http ssl headers php8.2 rewrite proxy_wstunnel

ENV APACHE_RUN_USER=www-data
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_PID_FILE=/var/run/apache2/apache2.pid
ENV APACHE_RUN_DIR=/var/run/apache2
ENV APACHE_LOCK_DIR=/var/lock/apache2
ENV APACHE_LOG_DIR=/var/log/apache2
ENV LANG=C

RUN mkdir -p /var/log/api && chmod 0644 /var/log/api && chown www-data /var/log/api

COPY ./idpFp.sh /idpFp.sh
RUN chmod +x /idpFp.sh

WORKDIR /

#anti-cache line
ADD "https://api.github.com/repos/humlab-speech/webclient/commits?per_page=1" latest_commit

RUN git clone https://github.com/humlab-speech/webclient
WORKDIR /webclient
RUN npm install --legacy-peer-deps
RUN npm run $WEBCLIENT_BUILD
RUN mv dist/* /var/www/html/

RUN git clone https://github.com/humlab-speech/webapi
RUN mkdir /var/www/html/api
RUN mv webapi/* /var/www/html/api

RUN mkdir /init_mongodb
ADD ./init_mongodb.js /init_mongodb/
ADD ./package.json /init_mongodb/
WORKDIR /init_mongodb
RUN npm install -legacy-peer-deps
RUN npm update --legacy-peer-deps

WORKDIR /var/www/html

USER root

CMD ["bash", "-c", "/etc/init.d/shibd start -w 3600 && /usr/sbin/apache2 -DFOREGROUND"]


EXPOSE 80
EXPOSE 443
