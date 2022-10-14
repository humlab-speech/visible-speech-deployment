FROM httpd:2.4

RUN apt-get update && apt-get install -y curl nano git

RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -

RUN apt-get install -y nodejs

RUN apt-get install -y yarnpkg

RUN mkdir /build

WORKDIR /build

RUN git clone https://github.com/FelixHenninger/lab.js.git

WORKDIR /build/lab.js

RUN yarnpkg && yarnpkg run bootstrap

WORKDIR /build/lab.js/packages/library

RUN yarnpkg

RUN yarnpkg run build:js

RUN yarnpkg run build:starterkit

WORKDIR /build/lab.js/packages/builder

ENV PUBLIC_URL=https://labjs.localtest.me

RUN yarnpkg run build

RUN cp -Rvp /build/lab.js/packages/builder/build/* /usr/local/apache2/htdocs/
