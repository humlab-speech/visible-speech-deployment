# Webclient Build Configuration

## Overview

The Angular webclient needs to know which domain it's deployed to at **build time** (not runtime). This is because the API endpoints and WebSocket URLs are hardcoded into the compiled JavaScript during the build process.

## How It Works

### 1. Environment Files

The webclient has different environment files for each deployment:

- `environment.visp.ts` - Main production (visp.humlab.umu.se)
- `environment.visp-demo.ts` - Demo server (visp-demo.humlab.umu.se)
- `environment.visp-pdf-server.ts` - PDF server (visp.pdf-server.humlab.umu.se)

Each environment file contains:
- `API_ENDPOINT` - The domain for API calls
- `BASE_DOMAIN` - The base domain for WebSocket connections

### 2. Build Configurations

The `angular.json` file maps npm scripts to environment files:

```json
"visp": {
  "fileReplacements": [{
    "replace": "src/environments/environment.ts",
    "with": "src/environments/environment.visp.ts"
  }]
}
```

### 3. Docker Build Argument

The `docker/apache/Dockerfile` accepts a build argument:

```dockerfile
ARG WEBCLIENT_BUILD=visp-build
ENV WEBCLIENT_BUILD=${WEBCLIENT_BUILD}
...
RUN npm run $WEBCLIENT_BUILD
```

### 4. Docker Compose Integration

Both `docker-compose.dev.yml` and `docker-compose.prod.yml` pass the build argument:

```yaml
apache:
  build:
    context: "./docker/apache"
    args:
      WEBCLIENT_BUILD: ${WEBCLIENT_BUILD:-visp-build}
```

### 5. .env Configuration

Set the build configuration in your `.env` file:

```bash
BASE_DOMAIN=visp.pdf-server.humlab.umu.se
WEBCLIENT_BUILD=visp-pdf-server-build
```

**IMPORTANT**: The `WEBCLIENT_BUILD` must match your `BASE_DOMAIN`:
- `visp.humlab.umu.se` → `WEBCLIENT_BUILD=visp-build`
- `visp-demo.humlab.umu.se` → `WEBCLIENT_BUILD=visp-demo-build`
- `visp.pdf-server.humlab.umu.se` → `WEBCLIENT_BUILD=visp-pdf-server-build`

## Deployment Process

1. **Update .env file** with correct `BASE_DOMAIN` and `WEBCLIENT_BUILD`
2. **Rebuild the apache image**: `docker compose build apache`
3. **Restart the container**: `docker compose up -d apache`

## Verification

Use the deployment script to check configuration:

```bash
python3 visp_deploy.py status
```

This will show:
- Expected build configuration from `.env`
- Expected domain from `.env`
- Actual domain in built files (if available)
- Whether they match

Example output:
```
📦 WEBCLIENT BUILD CONFIGURATION
+------------------+-------------------+----------------------------+------------------------+---------------+
| Setting          | Expected (.env)   | Expected Domain            | Actual Build           | Match Status  |
+==================+===================+============================+========================+===============+
| WEBCLIENT_BUILD  | visp-pdf-server-  | visp.pdf-server.humlab.    | Contains 'visp.pdf-    | ✅ CORRECT    |
|                  | build             | umu.se                     | server.humlab.umu.se'  |               |
+------------------+-------------------+----------------------------+------------------------+---------------+
```

## Troubleshooting

### Symptom: "e is null" errors in browser console

**Cause**: Angular app built with wrong domain configuration

**Solution**:
1. Check browser DevTools Network tab - look at WebSocket connection URL
2. If connecting to wrong domain, update `.env` with correct `WEBCLIENT_BUILD`
3. Rebuild: `docker compose build apache`
4. Restart: `docker compose up -d apache`

### Symptom: API calls return CORS errors

**Cause**: Built domain doesn't match deployment domain

**Solution**: Same as above - ensure `WEBCLIENT_BUILD` matches `BASE_DOMAIN`

## Development Mode

In development mode (`docker-compose.dev.yml`), the `external/webclient/dist` folder is mounted into the container. This means:

1. You can build locally: `cd external/webclient && npm run visp-build`
2. Changes appear immediately without rebuilding the Docker image
3. The `WEBCLIENT_BUILD` setting only affects Docker image builds

## Production Mode

In production mode (`docker-compose.prod.yml`), the webclient is built **inside** the Docker image during `docker compose build`. The source code is NOT mounted. This means:

1. `WEBCLIENT_BUILD` setting is critical
2. Must rebuild image after changing configuration
3. More secure - no source code on host needed

## Adding New Deployment Domains

To add a new domain:

1. **Create environment file**: `external/webclient/src/environments/environment.your-domain.ts`
2. **Add npm script**: In `package.json`, add `"your-domain-build": "ng build --configuration=your-domain --output-path dist"`
3. **Add Angular config**: In `angular.json`, add configuration with fileReplacements
4. **Update .env**: Set `WEBCLIENT_BUILD=your-domain-build`
5. **Rebuild**: `docker compose build apache`
