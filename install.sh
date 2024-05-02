#!/bin/bash

read -p "This will install the VISP system, assuming a development setup. The installation will seriously mess up any existing install. Do you wish to continue? (Y/N): " answer

if [[ $answer == [Yy] ]]; then
    echo "Very well."
elif [[ $answer == [Nn] ]]; then
    echo "It has been decided."
    exit 0
else
    echo "Invalid input. Please enter Y or N."
fi

set -e

sleep 2

echo "Installing dependencies"
apt update && apt install -y curl
curl -fsSL https://deb.nodesource.com/setup_20.x | bash
apt update && apt install -y nodejs git openssl docker.io docker-compose curl

echo "Copying .env-example to .env"
cp .env-example .env

echo "Creating files and directories log"
mkdir -p mounts/session-manager
touch  mounts/session-manager/session-manager.log
#session-manager is run inside a container based on node and the node user id is 1000
chown 1000 mounts/session-manager/session-manager.log
chmod 0644  mounts/session-manager/session-manager.log
mkdir -p mounts/webapi
mkdir -p mounts/apache/apache/uploads
mkdir -p mounts/mongo
mkdir -p certs

echo "Fetching SWAMID metadata signing cert"
curl http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt

echo "Generating local self-signed certificate for TLS"
mkdir certs/localtest.me
openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=localtest.me"

echo "Generating local self-signed certificate for internal IdP"
mkdir certs/ssp-idp-cert
openssl req -x509 -newkey rsa:4096 -keyout certs/ssp-idp-cert/key.pem -out certs/ssp-idp-cert/cert.pem -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=localtest.me"

echo "Grabbing latest webclient"
git clone https://github.com/humlab-speech/webclient

echo "Grabbing latest webapi"
git clone https://github.com/humlab-speech/webapi

echo "Grabbing latest container-agent"
git clone https://github.com/humlab-speech/container-agent

echo "Grabbing emu-webapp-server .env file"
mkdir -p mounts/emu-webapp-server/logs
curl -L https://raw.githubusercontent.com/humlab-speech/emu-webapp-server/main/.env-example -o ./mounts/emu-webapp-server/.env

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
mv simplesamlphp-1.19.6 ./mounts/simplesamlphp/
cp -Rv simplesamlphp-visp/* ./mounts/simplesamlphp/simplesamlphp/

echo "Setting directory permissions"
./set_permissions.sh


# Fill out .env to the extent that we can, with randomly generated passwords
#!/bin/bash

# Path to the .env file
env_file=".env"

# List of variables to fill automatically
declare -a keys_to_fill=("POSTGRES_PASSWORD" "TEST_USER_LOGIN_KEY" "VISP_API_ACCESS_TOKEN" "RSTUDIO_PASSWORD" "MONGO_ROOT_PASSWORD" "ELASTIC_AGENT_FLEET_ENROLLMENT_TOKEN" "MATOMO_DB_PASSWORD")

# Function to generate a random alphanumeric string of a given length
generate_random_string() {
  local len=$1
  # Generate random alphanumeric string (adjust the length as needed)
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c ${len} || true
}

# Check if the .env file exists
if [[ ! -f "$env_file" ]]; then
  echo "Error: .env file does not exist!"
  exit 1
fi

# Read each line from the .env file
while IFS= read -r line; do
  # Extract the key name from the line
  key=$(echo $line | cut -d '=' -f 1)
  
  # Check if the key is in the array of keys to fill
  if [[ " ${keys_to_fill[@]} " =~ " ${key} " && "$line" =~ =$ ]]; then
    # Generate a random alphanumeric string for the value (e.g., 16 characters long)
    random_value=$(generate_random_string 16)
    # Append the random value to the line
    new_line="${key}=${random_value}"
    # Replace the line in the file
    sed -i "s|${line}|${new_line}|g" "$env_file"
  fi
done < "$env_file"


echo 
echo "The .env file has been filled out with randomly generated passwords where possible. You should now look through and fill out the rest of it."
echo "Also fill out the separate emu-webapp-server .env file at: mounts/emu-webapp-server/.env."
echo "Meanwhile I will build the session-manager container images, which will take a while."
read -p "Press enter to continue: "


echo "Building Operations session image"
docker build -t visp-operations-session -f ./docker/session-manager/operations-session/Dockerfile ./docker/session-manager/operations-session

echo "Building RStudio session image"
docker build -t visp-rstudio-session -f ./docker/session-manager/rstudio-session/Dockerfile ./docker/session-manager/rstudio-session

#echo "Building Jupyter session image"
docker build -t visp-jupyter-session -f ./docker/session-manager/jupyter-session/Dockerfile ./docker/session-manager/jupyter-session

docker-compose build

echo "Development install complete. If everything above looks ok, you should now be able to run the project with 'docker-compose up -d'"
