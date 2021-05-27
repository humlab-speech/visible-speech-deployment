# VISP (the project formerly known as Humlab Speech)

This is a cluster of docker containers running the Humlab Speech services.

Included services:
* Edge router (Apache + Shibboleth) - Serves the main portal page, handles authentication towards Keycloak and integration with Gitlab API

* Gitlab

* Keycloak - Acts as an identity provider for authentication.

* PostgreSQL - Dependency of Keycloak.

* Session Manager - Spawns and manages session containers (such as RStudio and Jupyter) on request. Also handles dynamic proxying.

* EMU-webApp - Integrated via its own Gitlab functionality

* OCTRA - Local mode only (only hosted, not integrated)

* LabJS - Standalone

## INSTALLATION

### Prerequisites
A Linux environment with a somewhat recent version of Docker + Docker Compose.

If you are using WSL2, you will run into issues if you put this project inside an NTFS mount, such as /mnt/c, use a location inside the WSL2 container instead, such as ~/

## Quickstart
If you're feeling brave you can try running install.sh which will automate some of the below steps.

### Steps
1. Enter into humlab-speech-deployment directory
1. Copy .env-example to .env and fill it out with appropriate information.
1. Generate some local certificates. These would not be used in production, but we assume a local development installation here. `openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/cert.key -out certs/localtest.me/cert.crt -nodes -days 3650`
1. Grab latest webclient `git clone https://github.com/humlab-speech/webclient`
1. Grab latest webapi `git clone https://github.com/humlab-speech/webapi`
1. Grab latest container-agent `https://github.com/humlab-speech/container-agent`
1. Install & build container-agent `cd container-agent && npm install && npm run build && cd ..`
1. Install & build webclient `cd webclient && npm install && npm run build && cd ..`
1. Install Session-Manager `git clone https://github.com/humlab-speech/session-manager`
1. Make sure you have the PHP extension for MongoDB & Install vendors for webapi `cd webapi && php composer.phar install && cd ..`
1. Go to docker/session-manager and run `build-session-images.sh`. This will take some time and it's fine if this isn't completed before you proceed, so you might want to do this in a separate terminal.
1. Run `docker-compose up -d`
1. Got to the control panel for MongoDB at http://localhost:8081 and create a new database called `humlab_speech` with a collection called `personal_access_tokens`
1. Gitlab setup
  * Sign-in to Gitlab (at https://gitlab.localtest.me) with the root account.
  * Go to `settings` in your avatar menu.
  * Go to `Access Tokens`.
  * Create an access token with `api` access. Name doesn't matter. Enter this access token into your .env 
  * Edit .env and set `GITLAB_OMNIAUTH_AUTO_SIGN_IN_WITH_PROVIDER=saml` (revert this if you need to login as root at some future point)
1. Keycloak setup
  * Go to Keycloak at https://idp.localtest.me
  * Sign-in with the keycloak admin credentials you specified in .env
  * Create a realm called `hird` and import the keycloak-config.json file in the same step.
  * Run the included script `idpFp.sh`, this should print out your Keycloak IdP fingerprint. Enter this into your .env file like `KEYCLOAK_SIGNING_CERT_FINGERPRINT=42:31:C4:AF...`.  
1. Run `docker-compose down && docker-compose up -d` to let various services read in new data.

Everything should now be setup for using the system with Keycloak as the local identity provider. A user for testing has been created for you in Keycloak, you need to login to Keycloak as admin to set a password for it.
If you wish to add more test users, you can just do so in Keycloak, the minimum requirements are an email as well as the eppn_keycloak attribute, which can be the same as the email.

For a production setup you might want to add your organization as an identity provider in Keycloak.

## Extras
* You might want to go to https://gitlab.localtest.me/admin/application_settings/general#js-signup-settings and uncheck 'Sign-up enabled' to prevent the creation of local GitLab accounts.

* If you need to sign-in to GitLab as root again after enabling the auto-redirect to SAML/Keycloak, this is a useful URL: `https://gitlab.localtest.me/users/sign_in?auto_sign_in=false`

## TROUBLESHOOTING
* For errors about proxy timeouts when visiting gitlab, just wait a few minutes, gitlab takes a while to start.
