FROM gitlab/gitlab-ce:14.9.4-ce.0

RUN echo 'gitlab_workhorse_options="-listenUmask 0 -listenNetwork tcp -listenAddr 127.0.0.1:8181 -authBackend http://127.0.0.1:8080"' > /etc/default/gitlab

RUN apt-get update && apt-get install -y apache2

#Add www-data (apache user) to the gitlab-www group so that it may access the web root at /var/opt/gitlab/nginx
RUN usermod -a -G gitlab-www www-data

RUN a2enmod rewrite proxy proxy_http headers ssl env

RUN rm /etc/apache2/sites-enabled/000-default.conf

ENV APACHE_PID_FILE=/var/run/apache2.pid
ENV APACHE_RUN_USER=www-data
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_LOG_DIR=/var/log/apache2/

CMD /bin/bash -c "/etc/init.d/apache2 start && /assets/wrapper"
