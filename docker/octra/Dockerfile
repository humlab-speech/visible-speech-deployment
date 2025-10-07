# Stage 1: Build the project
FROM node:20.5.1 AS build

WORKDIR /app

# Install git and clean up afterwards
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone the specific release tag with minimal history
RUN git clone --branch web-media-1.0.1 --depth 1 https://github.com/IPS-LMU/octra.git .

# Install dependencies
RUN npm install --legacy-peer-deps

# Build the project
RUN npm run build:release

# Stage 2: Serve the built files with Apache HTTPD
FROM httpd:2.4.63

# Create the media directory
RUN mkdir -p /usr/local/apache2/htdocs/media

# Copy the built app
COPY --from=build /app/dist/apps/octra /usr/local/apache2/htdocs/

# Copy the demo file
COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav