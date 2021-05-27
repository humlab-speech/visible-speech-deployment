if ! command -v docker &> /dev/null
then
    echo "Docker could not be found. Please install docker.io"
    exit
fi
if ! command -v php &> /dev/null
then
    echo "PHP could not be found. Please install PHP-CLI 7.x"
    exit
fi

if ! command -v npm &> /dev/null
then
    echo "NPM could not be found. Please install a recent version of nodejs npm."
    exit
fi

if ! command -v git &> /dev/null
then
    echo "Git could not be found. Please install Git."
    exit
fi

if ! command -v openssl &> /dev/null
then
    echo "OpenSSL could not be found. Please install OpenSSL."
    exit
fi

echo "Copy .env-example to .env"
cp .env-example .env

echo "Generating local self-signed certificate"
openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650

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

echo "Skipping PHP WebApi install"

echo "You should now fill out .env as best you can and then do the rest of the install manually."

