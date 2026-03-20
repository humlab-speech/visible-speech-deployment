# syntax=docker/dockerfile:1

# ------------------------------------------------------------
# base: Apache + Shibboleth + PHP + Node + Composer
# ------------------------------------------------------------
FROM docker.io/library/debian:trixie AS base

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="Debian Apache + Shibboleth + PHP (Sury) + Node, for VISP webserver/router/auth layer"

ENV DEBIAN_FRONTEND=noninteractive

# Add Sury PHP repository for up-to-date PHP
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates lsb-release apt-transport-https wget gnupg && \
    wget -O /etc/apt/trusted.gpg.d/php.gpg https://packages.sury.org/php/apt.gpg && \
    echo "deb https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php.list

# Install system deps + Apache + Shibboleth + PHP
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git curl gnupg iputils-ping nano \
      libapache2-mod-shib ntpsec openssl \
      apache2 \
      php8.4 libapache2-mod-php8.4 php8.4-cli \
      php8.4-mbstring php8.4-zip php8.4-xml php8.4-pgsql php8.4-curl php8.4-mongodb \
      lsb-release wget && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 24 and tini
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs tini && \
    rm -rf /var/lib/apt/lists/*

# Enable required Apache modules (including PHP)
RUN a2enmod php8.4 proxy proxy_http ssl headers rewrite proxy_wstunnel

ENV APACHE_RUN_USER=www-data
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_PID_FILE=/var/run/apache2/apache2.pid
ENV APACHE_RUN_DIR=/var/run/apache2
ENV APACHE_LOCK_DIR=/var/lock/apache2
ENV APACHE_LOG_DIR=/var/log/apache2
ENV LANG=C

# log dir (permissions fix: directory should not be 0644)
RUN mkdir -p /var/log/api && chmod 0755 /var/log/api && chown -R www-data:www-data /var/log/api

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

# init_mongodb helper (kept in base so both dev/prod have it)
WORKDIR /init_mongodb
COPY ./docker/apache/init_mongodb.js /init_mongodb/
COPY ./docker/apache/package.json /init_mongodb/
RUN npm install --legacy-peer-deps && npm update --legacy-peer-deps

# Tini as PID 1
ENTRYPOINT ["/usr/bin/tini", "--"]

EXPOSE 80 443


# ------------------------------------------------------------
# webclient-build: builds the Angular webclient once (prod artifact)
# ------------------------------------------------------------
FROM base AS webclient-build

# Build argument used to choose npm script
ARG WEBCLIENT_BUILD=visp-dev
ENV WEBCLIENT_BUILD=${WEBCLIENT_BUILD}

# Optional cache-bust so you can force a fresh git clone when desired
ARG WEBCLIENT_GIT_REF=master
ARG CACHE_BUST=0

WORKDIR /webclient

# Clone and build
RUN echo "CACHE_BUST=$CACHE_BUST" && \
    git clone --depth 1 --branch "${WEBCLIENT_GIT_REF}" https://github.com/humlab-speech/webclient /webclient

RUN npm install --legacy-peer-deps

# Composer dependencies (reproducible)
RUN composer install --no-dev --prefer-dist --optimize-autoloader --no-interaction

RUN echo "Building webclient with build target: ${WEBCLIENT_BUILD}" && \
    npm run "${WEBCLIENT_BUILD}"

# Put build output in a neutral path we can copy from
RUN mkdir -p /out && cp -r dist/* /out/


# ------------------------------------------------------------
# production: runtime image serving prebuilt /var/www/html via Apache
# ------------------------------------------------------------
FROM base AS production

# If you have Apache site confs / shibboleth configs, COPY them here:
# COPY ./docker/apache/sites-enabled/*.conf /etc/apache2/sites-enabled/
# COPY ./docker/apache/shibboleth/* /etc/shibboleth/

WORKDIR /var/www/html

# Copy prebuilt frontend artifacts
COPY --from=webclient-build /out/ /var/www/html/

# Start shibd + apache (no watcher)
CMD ["bash", "-lc", "/etc/init.d/shibd start -w 3600 && exec /usr/sbin/apache2 -DFOREGROUND"]


# ------------------------------------------------------------
# development: runtime image running watch build into /var/www/html
# ------------------------------------------------------------
FROM base AS development

# NOTE:
# In dev, you will usually bind-mount your local repo to /webclient,
# so the clone below is only a fallback to make the image runnable standalone.
ARG WEBCLIENT_GIT_REF=master
ARG CACHE_BUST=0

WORKDIR /webclient
RUN echo "CACHE_BUST=$CACHE_BUST" && \
    git clone --depth 1 --branch "${WEBCLIENT_GIT_REF}" https://github.com/humlab-speech/webclient /webclient

RUN npm install --legacy-peer-deps
RUN composer install --no-dev --prefer-dist --optimize-autoloader --no-interaction

WORKDIR /var/www/html

# Watch build in background, Apache in foreground
# (polling makes file watching reliable with bind mounts)
ENV CHOKIDAR_USEPOLLING=true
ENV CHOKIDAR_INTERVAL=250

CMD ["bash", "-lc", "/etc/init.d/shibd start -w 3600 && cd /webclient && npx ng build --configuration=visp.dev --output-path /var/www/html --watch --poll=2000 & exec /usr/sbin/apache2 -DFOREGROUND"]
