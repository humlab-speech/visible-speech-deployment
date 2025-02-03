# Stage 1: Build the project
FROM node:20 AS build

# Set the working directory
WORKDIR /app

# Install Git (in case it's not included in the Node.js image)
RUN apt-get update && apt-get install -y git && apt-get clean

# Clone the repository and checkout a specific tag
RUN git clone --branch utilities-1.0.2 https://github.com/IPS-LMU/octra.git .

# Install dependencies
RUN npm install --legacy-peer-deps

# Run the build command
RUN npm run build:release

# Stage 2: Serve the built files with httpd
FROM httpd:2.4

# Copy the built files from the build stage to the httpd document root
COPY --from=build /app/dist/apps/octra /usr/local/apache2/htdocs/

# Copy additional files (e.g., demo_file.wav)
COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav
