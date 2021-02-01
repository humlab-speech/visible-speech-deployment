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

#Generate some local certificates. These would not be used in production, but we assume a local development installation here.
#openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650

#Grab latest webclient
git clone https://github.com/humlab-speech/webclient

#Grab latest webapi
git clone https://github.com/humlab-speech/webapi

#Install & build webclient
cd webclient
npm install && npm run build
cd ..

#Install vendors for webapi
cd webapi
php composer.phar install
cd ..


