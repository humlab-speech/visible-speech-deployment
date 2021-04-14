# Humlab Speech Deployment

This is a cluster of docker containers running the Humlab Speech services.

Included services:
* Edge router (Apache + Shibboleth) - Serves the main portal page, handles authentication towards Keycloak and integration with Gitlab API

* Gitlab

* Keycloak - Acts as an identity provider for authentication.

* PostgreSQL - Dependency of Keycloak.

* Session Manager - Spawns and manages session containers (such as RStudio and Jupyter) on request. Also handles dynamic proxying.

* EMU-webApp - Integrated via its own Gitlab functionality

* OCTRA - Local mode only (only hosted, not integrated)


## INSTALLATION

### Prerequisites
A Linux environment with a somewhat recent version of Docker + Docker Compose.

If you are using WSL2, you will run into issues if you put this project inside an NTFS mount, such as /mnt/c, use a location inside the WSL2 container instead, such as ~/

### Steps

* Move .env-example to .env and fill it out with appropriate information.
* Generate some local certificates. These would not be used in production, but we assume a local development installation here. `openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650`
* Grab latest webclient `git clone https://github.com/humlab-speech/webclient`
* Grab latest webapi `git clone https://github.com/humlab-speech/webapi`
* Install & build webclient `cd webclient && npm install && npm run build && cd ..`
* Make sure you have the PHP extension for MongoDB & Install vendors for webapi `cd webapi && php composer.phar install && cd ..`
* Go to docker/session-manager and run `build-session-images.sh`. This will take some time and it's fine if this isn't completed before you proceed, so you might want to do this in a separate terminal.
* Run `docker-compose up -d`
* Got to the control panel for MongoDB at http://localtest.me:8081 and create a new database called `humlab_speech` with a collection called `personal_access_tokens`
* Gitlab setup
  * Sign-in to Gitlab with the root account.
  * Go to `settings` in your avatar menu.
  * Go to `Access Tokens`.
  * Create an access token with `api` access. Name doesn't matter. Enter this access token into your .env 
  * Edit .env and set `GITLAB_OMNIAUTH_AUTO_SIGN_IN_WITH_PROVIDER=saml` (revert this if you need to login as root at some future point)
* Keycloak setup
  * Go to idp.localtest.me
  * Sign-in with the keycloak admin credentials you specified in .env
  * Create a realm called `hird` and import the keycloak-config.json file in the same step.
  * Run the included script `idpFp.sh`, this should print out your Keycloak IdP fingerprint. Enter this into your .env file like `KEYCLOAK_SIGNING_CERT_FINGERPRINT=42:31:C4:AF...`.  
* Run `docker-compose down && docker-compose up -d` to let various services read in new data.

Everything should now be setup for using the system with Keycloak as the local identity provider. You may create a normal user account in Keycloak to then use for sign-in at http://localtest.me

## TROUBLESHOOTING

* If you get a message about `Unknown or Unusable Identity Provider`, try restarting the edge-router. This is probably because Keycloak wasn't ready when the edge-router went up, preventing it from reading metadata from Keycloak.

* For errors about proxy timeouts when visiting gitlab, just wait a few minutes, gitlab takes a while to start.

