# syntax=docker/dockerfile:1
#
# arctic — Svelte 5 + Vite 8 build
# Upstream: https://github.com/humlab-speech/artic
#

############################
# 1) Base deps stage
############################
FROM docker.io/library/node:22-bookworm-slim AS deps
WORKDIR /app

# Install deps first (better caching)
COPY package*.json ./
RUN npm ci

############################
# 2) Development stage
############################
FROM docker.io/library/node:22-bookworm-slim AS development
WORKDIR /app

# Reuse node_modules from deps stage
COPY --from=deps /app/node_modules ./node_modules
COPY . .

EXPOSE 9000

CMD ["npx", "vite", "--host", "0.0.0.0", "--port", "9000"]

############################
# 3) Build stage (creates static assets)
############################
FROM docker.io/library/node:22-bookworm-slim AS build
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build:svelte

############################
# 4) Production stage (serves built assets)
############################
FROM docker.io/library/nginx:alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
