# Humlab Speech Cluster

This is a cluster of docker containers running the Humlab Speech services.

Included services:
* Edge router (Apache + Shibboleth) - Serves the main portal page and handles authentication as well as integration towards Gitlab

* Gitlab

* Keycloak - Acts as an identity provider for authentication.

* PostgreSQL - Dependency of Keycloak.

* RStudio router - Spawns and manages RStudio session containers on request by edge router.

* (non-functional) EMU-webApp + server


## INSTALLATION

### Prerequisites
A Linux environment with a somewhat recent version of Docker + Docker Compose. WSL2 on Windows should work.

### Steps

1. Move .env-example to .env and fill it out with appropriate information.

2. Generate self-signed certificates by running `./gen-certs.sh`

3. Run `docker-compose up -d`

5. Gitlab setup
  5.1 Go to gitlab.localtest.me, gitlab will take a couple of minutes to boot but then you should be greeted with a password dialog, enter a new root password here.
  5.2 Sign-in to Gitlab with the root account. 
  5.3 Go to `settings` in your avatar menu.
  5.4 Go to `Access Tokens`.
  5.5 Create an access token with `api` access. Name doesn't matter. Enter this access token into your .env 

6. Keycloak setup
  6.1 Go to idp.localtest.me
  6.2 Sign-in with the keycloak admin credentials you specified in .env
  6.3 Create a realm called `Hird`
  6.4 Go to `Clients`, create a client with Client ID `https://localtest.me` and base url `https://localtest.me`
  6.5 Edit the newly created client, set `Client Signature Required: OFF`
  6.6 Go to `Mappers` tab. Add (at minimum) attribute mapper for X500 email and edit it to set "SAML Attribute NameFormat" as "URI Reference"

7. Webclient setup
  7.1 Go to the `webclient` directory.
  7.2 Run `npm install`
  7.3 Run `npm run build` - It's ok to exit this process after it seems to be done, it will go into watch mode and thus won't auto-exit.

Everything should now be setup for using the system with Keycloak as the local identity provider. You may create a normal user account in Keycloak to then use for sign-in at http://localtest.me

## CAVEATS ON REDHAT

* Not using latest version of Docker since DNF would not install the latest containerd.io because of how module-streams are configured.

* docker-compose is installed as a static/manually pulled binary, and thus won't auto-update, it was installed using this command:
  `sudo curl -L "https://github.com/docker/compose/releases/download/1.26.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose`

* Needed to do this (on RH) to get container-to-container networking to work:
  `sudo firewall-cmd --zone=public --add-masquerade --permanent`

## Optional

Select an identity provider by uncommenting/commenting out the approrpriate sections in docker-compose.yml. Recommend using keycloak for running locally since local users can be created in keycloak. SAMLtest is also an alternative.
SWAMID will not work for running locally since you can't have your local address registered as a SP with SWAMID.


### STARTING IT UP

`docker-compose up -d`

* It will take some time for the cluster to boot up. Gitlab in particular has a long boot time, as long as a few minutes. It can also take a long time for the edge-router to load the IdP metdata depending on your selected IdP. SWAMID has a rather huge metadata file. Should be a lot quicker for just running local Keycloak or SAMLtest.

