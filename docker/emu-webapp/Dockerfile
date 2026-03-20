# syntax=docker/dockerfile:1

############################
# 1) Base deps stage
############################
FROM docker.io/library/node:18-bookworm AS deps
WORKDIR /app

# Install deps first (better caching)
COPY package*.json ./
RUN npm ci

############################
# 2) Development stage
############################
FROM docker.io/library/node:18-bookworm AS development
WORKDIR /app

# Reuse node_modules from deps stage
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Webpack 4 + newer OpenSSL often needs this
ENV NODE_OPTIONS=--openssl-legacy-provider

# If bind-mount file events are flaky, polling makes rebuild reliable
ENV CHOKIDAR_USEPOLLING=true
ENV CHOKIDAR_INTERVAL=250

EXPOSE 9000

CMD ["npx", "webpack-dev-server", "--config", "webpack.dev.js", "--host", "0.0.0.0", "--port", "9000"]

############################
# 3) Build stage (creates static assets)
############################
FROM docker.io/library/node:18-bookworm AS build
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NODE_OPTIONS=--openssl-legacy-provider

# IMPORTANT: replace with your real production build command
# e.g. "npm run build" should produce /app/dist (or /app/build)
RUN npm run build

############################
# 4) Production stage (serves built assets)
############################
FROM docker.io/library/nginx:alpine AS production

# If your build output is /app/dist, copy it into nginx web root:
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Default nginx command is fine
