FROM httpd:2.4

# COPY ./public-html/ /usr/local/apache2/htdocs/


RUN apt-get update && apt-get -y install nginx curl

COPY cacert.pem /etc/ssl/certs/cacert.pem

COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav

WORKDIR /

RUN curl -k -OL https://github.com/IPS-LMU/octra/releases/download/v1.4.3/octra-v1-4-3.zip

RUN apt-get update && apt-get -y install unzip

RUN unzip octra-v1-4-3.zip -d /

# Files in the zip file are in an /octra dir, so no need to create it...
WORKDIR /octra/

#RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -

RUN cp -R ./* /usr/local/apache2/htdocs/



