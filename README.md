
# Visible Speech

This is a cluster of services in the form of docker containers, which as a whole makes out the Visible Speech (VISP) system.

Included services:
* Traefik
  * Edge router

* Webserver
  * Apache + Shibboleth - Serves the main portal page, handles authentication towards Keycloak and integration with Gitlab API

* Gitlab

* SimpleSamlPhp - Acts as an identity provider for internal authentication. Currently only used by gitlab.

* Session Manager - Spawns and manages session containers (such as RStudio and Jupyter) on request. Also handles dynamic routing of network traffic into these containers.

* EMU-webApp - Integrated via its own Gitlab functionality

* OCTRA - Local mode only (only hosted, not integrated)

* LabJS - Standalone

## INSTALLATION

### Prerequisites
A Linux environment based on Debian or Ubuntu.

If you are using WSL2, you will run into issues if you put this project inside an NTFS mount, such as `/mnt/c`, use a location inside the WSL2 container instead, such as `~/`. Note that you need to have docker and docker-compose available.

### Steps
1. Enter into visible-speech-deployment directory. The instructions will assume this is where you are currently standing from now on.
1. RUN `sudo install.sh`
1. Fill out your `.env` file with the appropriate information.
1. Go to docker/session-manager and run `build-session-images.sh`. This will take some time and it's fine if this isn't completed before you proceed, so you might want to do this in a separate terminal.
1. Run `docker-compose up -d`
1. Got to the control panel for MongoDB at http://localhost:8081 and create a new database called `visp` with a collection called `personal_access_tokens`
1. Gitlab setup
  * Sign-in to Gitlab (at https://gitlab.localtest.me) with the root account.
  * Go to `preferences` in your avatar menu.
  * Go to `Access Tokens`.
  * Create an access token with `api` access. Name doesn't matter. Enter this access token into your .env 
  * Edit .env and set `GITLAB_OMNIAUTH_AUTO_SIGN_IN_WITH_PROVIDER=saml` (revert this if you need to login as root at some future point)
1. RUN `docker-compose exec apache bash /idpFp.sh` to get your internal IdP fingerprint and use this to fill in `IDP_SIGNING_CERT_FINGERPRINT` in .env.
1. Keycloak setup
  * Go to Keycloak at https://idp.localtest.me
  * Sign-in with the keycloak admin credentials you specified in .env
  * Create a realm called `hird` and import the keycloak-config.json file in the same step.
  * Run `docker-compose exec apache bash idpFp.sh`, this should print out your Keycloak IdP fingerprint. Enter this into your .env file like `KEYCLOAK_SIGNING_CERT_FINGERPRINT=42:31:C4:AF...`.  
1. Run `docker-compose down && docker-compose up -d` to let various services read in new data.

Everything should now be setup for using the system with Keycloak as the local identity provider. A user for testing has been created for you in Keycloak, you need to login to Keycloak as admin to set a password for it.
If you wish to add more test users, you can just do so in Keycloak, the minimum requirements are an email as well as the eppn_keycloak attribute, which can be the same as the email.

For a production setup you might want to add your organization as an identity provider in Keycloak.

## Extras
* You might want to go to https://gitlab.localtest.me/admin/application_settings/general#js-signup-settings and uncheck 'Sign-up enabled' to prevent the creation of local GitLab accounts.

* If you need to sign-in to GitLab as root again after enabling the auto-redirect to SAML/Keycloak, this is a useful URL: `https://gitlab.localtest.me/users/sign_in?auto_sign_in=false`

## TROUBLESHOOTING
* For errors about proxy timeouts when visiting gitlab, just wait a few minutes, gitlab takes a while to start.

## Manual installation
These are the steps performed by the install script:
1. Copy .env-example to .env and fill it out with appropriate information.
1. Generate some local certificates. These would not be used in production, but we assume a local development installation here. `openssl req -x509 -newkey rsa:4096 -keyout certs/localtest.me/c>1. Grab latest webclient `git clone https://github.com/humlab-speech/webclient`
1. Grab latest webapi `git clone https://github.com/humlab-speech/webapi`
1. Grab latest container-agent `git clone https://github.com/humlab-speech/container-agent`
1. Install & build container-agent `cd container-agent && npm install && npm run build && cd ..`
1. Install & build webclient `cd webclient && npm install && npm run build && cd ..`
1. Install Session-Manager `git clone https://github.com/humlab-speech/session-manager`
