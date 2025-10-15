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

COPY EMU-webApp/package.json EMU-webApp/package-lock.json ./

RUN npm ci

COPY EMU-webApp/. .

# This fixes errors with using older ciphers... for now.
RUN NODE_OPTIONS=--openssl-legacy-provider npm run build

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
