# REFACTORING TODO

This document outlines the refactoring tasks needed to modernize the visible-speech-deployment project by removing legacy dependencies and containerizing remaining components.

## ✅ COMPLETED: GitLab References Removal

### Code References - COMPLETED
- **webapi/api.php**: Removed GitLab API integration code including:
  - GitLab user authentication
  - Project management via GitLab API
  - Personal access token handling
  - GitLab project fetching and creation

- **webapi/SessionManagerInterface.class.php**: Removed GitLab-related methods:
  - `_fetchGitlabProjectById()`
  - `isGitlabReady()`
  - GitLab API request handling
  - Modified session creation to use Keycloak user info instead of GitLab

- **session-manager/src/ApiServer.class.js**: Removed GitLab integration:
  - Removed `gitLabActivated` flag and logic
  - Modified session creation endpoints to expect `user` and `projectId` instead of `gitlabUser` and `project`
  - Removed GitLab git cloning logic
  - Removed GitLab environment variables

- **session-manager/src/index.js**: Removed GitLab environment variables

- **container-agent/src/GitRepository.class.mjs**: Updated error messages to be GitLab-agnostic

### Configuration References - COMPLETED
- **docker-compose.yml**: Removed GitLab-related environment variables:
  - `GITLAB_DOMAIN_NAME`
  - `GIT_API_ACCESS_TOKEN`
  - `GITLAB_ACTIVATED`

- **docker-compose.dev.yml**: Removed GitLab-related environment variables

- **docker-compose.prod.yml**: Removed GitLab-related environment variables

- **Traefik routing**: Removed GitLab domain from active routing configuration

- **.gitignore**: Cleaned up GitLab-related ignore patterns

- **README.md**: Updated documentation to remove GitLab references

- **.env-example**: Updated comments to remove GitLab references

- **simplesamlphp-visp/metadata/saml20-sp-remote.php**: Removed GitLab SAML service provider configuration

### Files Removed - COMPLETED
- `mounts/apache/apache/vhosts-https/gitlab.vhost.conf`
- `mounts/gitlab/` directory (entire GitLab mounts directory)

## Remaining Tasks

### Code References
- **webapi/api.php**: Remove GitLab API integration code including:
  - GitLab user authentication
  - Project management via GitLab API
  - Personal access token handling
  - GitLab project fetching and creation

- **webapi/SessionManagerInterface.class.php**: Remove GitLab-related methods:
  - `_fetchGitlabProjectById()`
  - GitLab API request handling
  - GitLab user/project synchronization

- **session-manager/src/ApiServer.class.js**: Remove GitLab integration:
  - GitLab API endpoints
  - GitLab user/project management
  - GitLab repository operations

### Configuration References
- **docker-compose.yml**: Remove GitLab-related environment variables:
  - `GITLAB_DOMAIN_NAME`
  - `GIT_API_ACCESS_TOKEN`
  - `GITLAB_ACTIVATED`

- **Apache virtual hosts**: Remove GitLab domain references in vhost configurations

- **Environment variables**: Clean up any GitLab-related env vars in deployment scripts

### Documentation
- **README.md**: Update installation instructions to remove GitLab cloning steps

### Deployment Scripts
- **install.sh**: Remove GitLab repository cloning
- **visp_deploy.py**: Remove GitLab repository handling
- **update.py**: Remove GitLab-related update logic

## SimpleSAMLphp Removal ✅ COMPLETED

Since SimpleSAMLphp is only used as an identity provider for GitLab authentication, it can be completely removed:

### Files Removed ✅
- **`simplesamlphp-visp/`** directory (entire configuration directory) - deleted
- **`mounts/simplesamlphp/`** directory (runtime installation) - deleted

### Code References Removed ✅
- **install.sh**: Remove SimpleSAMLphp download and installation steps - removed
- **visp_deploy.py**: Remove SimpleSAMLphp installation logic - removed
- **docker-compose.prod.yml**: Remove SimpleSAMLphp volume mounts - removed
- **.gitignore**: Remove `mounts/simplesamlphp` entry - removed
- **.pre-commit-config.yaml**: Remove SimpleSAMLphp-related exclusions - removed
- **README.md**: Remove SimpleSAMLphp service description - removed
- **docker/apache/Dockerfile**: Remove idpFp.sh script copy - removed
- **docker/apache/idpFp.sh**: Delete script file - deleted
- **.gitignore.save**: Remove wsrng-server/mounts/simplesamlphp entry - removed

### Configuration Cleanup ✅
- Removed any SimpleSAMLphp-related environment variables
- Cleaned up deployment scripts that reference SimpleSAMLphp

**Note**: Shibboleth remains in use for main authentication flow (Shibboleth SP → SWAMID IdP), so do not remove Shibboleth components.

## ✅ COMPLETED: Keycloak References Cleanup

**Keycloak references have been removed.** The system uses Shibboleth SP → SWAMID IdP for authentication. Keycloak was never actually implemented - it was just configuration remnants.

**Completed tasks:**
- ✅ Removed `KEYCLOAK_DOMAIN_NAME` environment variables from all docker-compose files
- ✅ Deleted `idp.vhost.conf` (proxied to non-existent Keycloak service)
- ✅ Cleaned up `.gitignore` entries for Keycloak files
- ✅ Updated README.md to clarify Shibboleth + SWAMID authentication
- ✅ Removed Keycloak references from documentation

## Containerization of Cloned Git Repositories**Current Authentication**: Shibboleth Service Provider → SWAMID Identity Federation (Swedish academic federation)

## Containerization of Cloned Git Repositories

Currently, several components are cloned as git repositories and run directly on the host with npm/node commands. To improve security, maintainability, and avoid running npm on the host, these should be containerized:

### Current Host-Run Components
- **webapi**: Currently cloned and mounted into Apache container
- **webclient**: Angular app built and served via Apache
- **session-manager**: Node.js application
- **wsrng-server**: Node.js application

### Containerization Strategy

#### Research Required
- **Multi-stage Docker builds**: For building Node.js apps in containers without leaving build artifacts
- **Docker Compose service integration**: How to properly integrate containerized Node.js services with existing Apache/Traefik routing
- **Volume mounting vs. container builds**: Whether to build containers at runtime or use pre-built images
- **Development workflow**: How to handle hot reloading and development when containerized

#### Potential Approaches
1. **Full containerization**: Build separate Docker images for each Node.js service
2. **Build-time containerization**: Use Docker for builds, but still mount built assets
3. **Hybrid approach**: Containerize services but keep web assets mounted

#### Implementation Considerations
- **Base images**: Choose appropriate Node.js base images (Alpine for smaller size)
- **Build caching**: Optimize Docker layer caching for faster builds
- **Environment handling**: Pass environment variables properly to containers
- **Logging**: Ensure container logs are accessible
- **Health checks**: Implement proper health checks for containerized services

#### Security Benefits
- No npm/node installation required on host
- Isolated build environments
- Consistent runtime environments
- Easier dependency management

### Migration Steps
1. Research and choose containerization approach
2. Create Dockerfiles for each Node.js service
3. Update docker-compose.yml to use containerized services
4. Test integration with existing Apache/Traefik setup
5. Remove git cloning from deployment scripts
6. Update documentation for new containerized workflow

## Post-GitLab Removal Assessment

After removing all GitLab references, evaluate whether the **webapi** component is still needed:

### Current webapi Functionality
- User authentication and session management
- File upload handling
- Session launching (operations, RStudio, Jupyter, VS Code, Emu-webapp)
- MongoDB integration for user data

### Potential webapi Removal Candidates
If GitLab integration was the primary purpose, consider:
- Migrating authentication to a different system (SAML/Shibboleth already handles this)
- Moving session management directly to session-manager service
- Handling file uploads via session-manager or direct client-server communication

### Decision Criteria
- If webapi only handled GitLab integration, it may be obsolete
- If it provides essential API endpoints for the webclient, keep it
- Consider consolidating API endpoints into the session-manager for simplicity

## Implementation Steps

1. Create backup branch before cleanup
2. Remove GitLab references systematically
3. ✅ Remove SimpleSAMLphp components
4. Research containerization approaches for Node.js services
5. Implement containerization of cloned repositories
6. Test authentication and session launching still work
7. Assess webapi necessity
8. Remove webapi if no longer needed, or refactor if partially needed
9. Update documentation and deployment scripts

## Testing Checklist

- [ ] User authentication via SAML/Shibboleth
- [ ] Session launching for all application types
- [ ] File uploads
- [ ] User session persistence
- [ ] MongoDB connectivity
- [ ] WebSocket communication (if webapi removed)
- [ ] Containerized services start and communicate properly
- [ ] No npm/node dependencies on host

## GitLab References to Remove

### Code References
- **webapi/api.php**: Remove GitLab API integration code including:
  - GitLab user authentication
  - Project management via GitLab API
  - Personal access token handling
  - GitLab project fetching and creation

- **webapi/SessionManagerInterface.class.php**: Remove GitLab-related methods:
  - `_fetchGitlabProjectById()`
  - GitLab API request handling
  - GitLab user/project synchronization

- **session-manager/src/ApiServer.class.js**: Remove GitLab integration:
  - GitLab API endpoints
  - GitLab user/project management
  - GitLab repository operations

### Configuration References
- **docker-compose.yml**: Remove GitLab-related environment variables:
  - `GITLAB_DOMAIN_NAME`
  - `GIT_API_ACCESS_TOKEN`
  - `GITLAB_ACTIVATED`

- **Apache virtual hosts**: Remove GitLab domain references in vhost configurations

- **Environment variables**: Clean up any GitLab-related env vars in deployment scripts

### Documentation
- **README.md**: Update installation instructions to remove GitLab cloning steps

### Deployment Scripts
- **install.sh**: Remove GitLab repository cloning
- **visp_deploy.py**: Remove GitLab repository handling
- **update.py**: Remove GitLab-related update logic

## SimpleSAMLphp Removal ✅ COMPLETED

Since SimpleSAMLphp is only used as an identity provider for GitLab authentication, it can be completely removed:

### Files Removed ✅
- **`simplesamlphp-visp/`** directory (entire configuration directory) - deleted
- **`mounts/simplesamlphp/`** directory (runtime installation) - deleted

### Code References Removed ✅
- **install.sh**: Remove SimpleSAMLphp download and installation steps - removed
- **visp_deploy.py**: Remove SimpleSAMLphp installation logic - removed
- **docker-compose.prod.yml**: Remove SimpleSAMLphp volume mounts - removed
- **.gitignore**: Remove `mounts/simplesamlphp` entry - removed
- **.pre-commit-config.yaml**: Remove SimpleSAMLphp-related exclusions - removed
- **README.md**: Remove SimpleSAMLphp service description - removed
- **docker/apache/Dockerfile**: Remove idpFp.sh script copy - removed
- **docker/apache/idpFp.sh**: Delete script file - deleted
- **.gitignore.save**: Remove wsrng-server/mounts/simplesamlphp entry - removed

### Configuration Cleanup ✅
- Removed any SimpleSAMLphp-related environment variables
- Cleaned up deployment scripts that reference SimpleSAMLphp

**Note**: Shibboleth remains in use for main authentication flow (Shibboleth SP → SWAMID IdP), so do not remove Shibboleth components.

## Post-GitLab Removal Assessment

After removing all GitLab references, evaluate whether the **webapi** component is still needed:

### Current webapi Functionality
- User authentication and session management
- File upload handling
- Session launching (operations, RStudio, Jupyter, VS Code, Emu-webapp)
- MongoDB integration for user data

### Potential webapi Removal Candidates
If GitLab integration was the primary purpose, consider:
- Migrating authentication to a different system (SAML/Shibboleth already handles this)
- Moving session management directly to session-manager service
- Handling file uploads via session-manager or direct client-server communication

### Decision Criteria
- If webapi only handled GitLab integration, it may be obsolete
- If it provides essential API endpoints for the webclient, keep it
- Consider consolidating API endpoints into the session-manager for simplicity

## Implementation Steps

1. Create backup branch before cleanup
2. Remove GitLab references systematically
3. Remove SimpleSAMLphp components
4. Test authentication and session launching still work
5. Assess webapi necessity
6. Remove webapi if no longer needed, or refactor if partially needed
7. Update documentation and deployment scripts

## Testing Checklist

- [ ] User authentication via SAML/Shibboleth
- [ ] Session launching for all application types
- [ ] File uploads
- [ ] User session persistence
- [ ] MongoDB connectivity
- [ ] WebSocket communication (if webapi removed)

## Risks

- Breaking authentication flow
- Losing session management capabilities
- Breaking file upload functionality
- Incompatibilities with existing user sessions
