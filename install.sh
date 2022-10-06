echo "Setting up nodejs repo and installing dependencies"
curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \ &&
apt install -y nodejs git openssl docker.io docker-compose

echo "Copy .env-example to .env"
cp .env-example .env

echo "Fetching SWAMID metadata signing cert"
curl http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt

echo "Generating local self-signed certificate for TLS"
mkdir certs/localtest.me
openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=localtest.me"

echo "Generating local self-signed certificate for internal IdP"
mkdir certs/ssp-idp-cert
openssl req -x509 -newkey rsa:4096 -keyout certs/ssp-idp-cert/key.pem -out certs/ssp-idp-cert/cert.pem -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=localtest.me"

echo "Grab latest webclient"
git clone https://github.com/humlab-speech/webclient

echo "Grab latest webapi"
git clone https://github.com/humlab-speech/webapi

echo "Grab latest container-agent"
git clone https://github.com/humlab-speech/container-agent

echo "Install & build container-agent"
cd container-agent && npm install && npm run build && cd ..

echo "Install & build webclient"
cd webclient && npm install && npm run build && cd ..

echo "Install Session-Manager"
git clone https://github.com/humlab-speech/session-manager
cd session-manager && npm install && cd ..

echo "Installing SimpleSamlPhp"
curl -L https://github.com/simplesamlphp/simplesamlphp/releases/download/v1.19.6/simplesamlphp-1.19.6.tar.gz --output simplesamlphp.tar.gz
tar xzf simplesamlphp.tar.gz && rm simplesamlphp.tar.gz
mv simplesamlphp-* simplesamlphp
mv simplesamlphp ./mounts/simplesamlphp/
cp -Rv simplesamlphp-visp/* ./mounts/simplesamlphp/simplesamlphp/

echo "You should now fill out .env as best you can and then do the rest of the install manually."
