# =========================================================================
# Stage 1: The "Builder" Stage
# =========================================================================
FROM node:18-bookworm AS builder

RUN apt-get update && \
    apt-get install -y curl ca-certificates gnupg git nano wget rubygems ruby-dev build-essential xsel && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN gem install sass
RUN gem install compass

COPY package.json package-lock.json ./

RUN npm ci

COPY . .

# Ensure node_modules binaries are executable after COPY (fixes permission issues on some systems)
RUN chmod -R +x node_modules/.bin/ || true

# Clean dist directory manually and build (avoids rimraf permission issues)
RUN rm -rf dist/* && NODE_OPTIONS=--openssl-legacy-provider npx webpack --config webpack.prod.js

# =========================================================================
# Stage 2: The "Final" Stage
# =========================================================================
FROM node:18-slim AS final

WORKDIR /app

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist

EXPOSE 9000

CMD ["npx", "serve", "-s", "dist", "-l", "9000"]
