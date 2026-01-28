FROM debian:bookworm

# Build argument that can be passed from docker-compose
ARG WEBCLIENT_BUILD=visp-build
ENV WEBCLIENT_BUILD=${WEBCLIENT_BUILD}

ARG MODE=dev
ENV MODE=${MODE}

RUN echo "WEBCLIENT_BUILD is set to ${WEBCLIENT_BUILD}"
RUN echo "MODE is set to ${MODE}"

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

RUN phpenmod mongodb

RUN a2enmod proxy proxy_http ssl headers php8.2 rewrite proxy_wstunnel

ENV APACHE_RUN_USER=www-data
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_PID_FILE=/var/run/apache2/apache2.pid
ENV APACHE_RUN_DIR=/var/run/apache2
ENV APACHE_LOCK_DIR=/var/lock/apache2
ENV APACHE_LOG_DIR=/var/log/apache2
ENV LANG=C

RUN mkdir -p /var/log/api && chmod 0644 /var/log/api && chown www-data /var/log/api

WORKDIR /

#prefer a local webclient (for air-gapped builds) if provided in the build context
COPY external/webclient /webclient
#anti-cache line
ADD "https://api.github.com/repos/humlab-speech/webclient/commits?per_page=1" latest_commit

# If a local webclient folder exists, use it. Otherwise clone from GitHub.
RUN if [ -d /webclient ] && [ "$(ls -A /webclient 2>/dev/null || true)" != "" ]; then \
			echo "Using local webclient from build context"; \
			cd /webclient && if [ -d dist ] && [ "$(ls -A dist 2>/dev/null || true)" != "" ]; then \
				echo "Using prebuilt dist in /webclient/dist"; \
			else \
				echo "No dist found, building webclient inside image"; npm install --legacy-peer-deps && npm run $WEBCLIENT_BUILD; \
			fi && mv dist/* /var/www/html/; \
		else \
			echo "Cloning webclient from GitHub"; git clone https://github.com/humlab-speech/webclient /tmp/webclient && cd /tmp/webclient && npm install --legacy-peer-deps && npm run $WEBCLIENT_BUILD && mv dist/* /var/www/html/; \
		fi

# If local fonts are provided under external/webclient/fonts, copy them into the site and
# remove external @import Google Fonts lines so the app uses local fonts only.
RUN if [ -d /webclient/fonts ] && [ "$(ls -A /webclient/fonts 2>/dev/null || true)" != "" ]; then \
			mkdir -p /var/www/html/assets/fonts && cp -r /webclient/fonts/* /var/www/html/assets/fonts/; \
			if [ -f /webclient/fonts.css ]; then \
				cp /webclient/fonts.css /var/www/html/assets/fonts.css; \
				# ensure index.html loads fonts.css (only if not already present)
				grep -q "assets/fonts.css" /var/www/html/index.html || sed -i '/<head>/a <link rel="stylesheet" href="assets/fonts.css">' /var/www/html/index.html || true; \
			fi; \
			# remove remote @import lines so fonts are not fetched from Google at runtime
			if [ -f /var/www/html/main.js ]; then \
				sed -i -E 's/@import"https:\/\/fonts.googleapis.com[^\"]*";//g' /var/www/html/main.js || true; \
			fi; \
		fi

RUN mkdir /init_mongodb
ADD ./docker/apache/init_mongodb.js /init_mongodb/
ADD ./docker/apache/package.json /init_mongodb/
WORKDIR /init_mongodb
RUN npm install --legacy-peer-deps
RUN npm update --legacy-peer-deps

WORKDIR /var/www/html/api

# install php dependencies via composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
RUN composer install --no-dev --optimize-autoloader
RUN composer dump-autoload --optimize

WORKDIR /var/www/html

USER root

# NOTE: Dev mode used to run 'ng build --watch' on startup, but this caused permission conflicts
# with the mounted dist/ directory. Builds are now handled externally via visp-podman.py build.
# The container just serves pre-built files from the mounted /var/www/html directory.
# TODO: Reconsider dev mode build strategy - in-container watch vs external rebuild command
CMD ["bash", "-c", "/etc/init.d/shibd start -w 3600 && /usr/sbin/apache2 -DFOREGROUND"]


EXPOSE 80
EXPOSE 443
