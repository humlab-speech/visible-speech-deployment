# Stage 1: Build the project
FROM docker.io/node:24.15.0 AS build

WORKDIR /app

# Install git and clean up afterwards
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone the humlab-speech fork, pinned to a specific commit
RUN git clone --branch main --depth 1 https://github.com/humlab-speech/octra.git . && \
    git checkout 4fb2c78496dd33925e10c411336abbf845054bca

# Raise Angular initial bundle budget (default 2MB is too low for this app)
RUN sed -i 's/"maximumWarning": "2mb"/"maximumWarning": "6mb"/' apps/octra/project.json

# Install dependencies
RUN npm install

# Build the project
RUN npm run build:release

# Stage 2: Serve the built files with Apache HTTPD
FROM docker.io/httpd:2.4.63

# Create the media directory
RUN mkdir -p /usr/local/apache2/htdocs/media

# Copy the built app
COPY --from=build /app/dist/apps/octra /usr/local/apache2/htdocs/

# Copy the demo file
COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav
