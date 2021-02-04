FROM debian:buster

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"

LABEL description="A debian based installation of apache with shibboleth, acting as the HIRD edge-router and authentication layer"

#openssl is only needed for shib-keygen, and php-cli is just to ease with debugging
#docker.io is for being able to manipulate containers in the outside cluster - for rstudio session containers initially
RUN apt-get update && apt-get install apache2 libapache2-mod-shib libapache2-mod-php ntp openssl php-cli libapache2-mod-php docker.io nano curl php7.3-mbstring --no-install-recommends -y

RUN a2enmod proxy proxy_http ssl headers php7.3 rewrite proxy_wstunnel

ENV APACHE_RUN_USER=www-data
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_PID_FILE=/var/run/apache2/apache2.pid
ENV APACHE_RUN_DIR=/var/run/apache2
ENV APACHE_LOCK_DIR=/var/lock/apache2
ENV APACHE_LOG_DIR=/var/log/apache2
ENV LANG=C

RUN mkdir -p /var/log/api && chmod 0644 /var/log/api && chown www-data /var/log/api

COPY ./wait-for-it.sh /wait-for-it.sh

CMD bash -c "/wait-for-it.sh -t 60 keycloak:8080 -- /etc/init.d/shibd start" && /usr/sbin/apache2 -DFOREGROUND

EXPOSE 80
EXPOSE 443
