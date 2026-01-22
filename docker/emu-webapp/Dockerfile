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

# Clone EMU-webApp from GitHub
RUN git clone https://github.com/humlab-speech/EMU-webApp.git /app

# Install dependencies
RUN npm ci

# Ensure node_modules binaries are executable after COPY (fixes permission issues on some systems)
RUN chmod -R +x node_modules/.bin/ || true

# Clean dist directory manually and build (avoids rimraf permission issues)
RUN rm -rf dist/* && NODE_OPTIONS=--openssl-legacy-provider npx webpack --config webpack.prod.js

# =========================================================================
# Stage 2: The "Production" Stage - Nginx
# =========================================================================
FROM nginx:alpine AS production

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx-production.conf /etc/nginx/conf.d/default.conf

EXPOSE 9000

CMD ["nginx", "-g", "daemon off;"]

# =========================================================================
# Stage 3: The "Development" Stage - With Watch Mode
# =========================================================================
FROM node:18-bookworm AS development

RUN apt-get update && \
    apt-get install -y curl ca-certificates gnupg git nano wget rubygems ruby-dev build-essential xsel nginx && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN gem install sass
RUN gem install compass

# Clone EMU-webApp from GitHub
RUN git clone https://github.com/humlab-speech/EMU-webApp.git /app

# Install dependencies
RUN npm ci

# Ensure node_modules binaries are executable
RUN chmod -R +x node_modules/.bin/ || true

# Copy nginx configuration for development
COPY nginx-development.conf /etc/nginx/sites-available/default

# Copy and setup start script
COPY start-dev.sh /usr/local/bin/start-dev.sh
RUN chmod +x /usr/local/bin/start-dev.sh

EXPOSE 9000

CMD ["/usr/local/bin/start-dev.sh"]
