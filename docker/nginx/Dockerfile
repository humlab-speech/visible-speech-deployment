FROM docker.io/library/nginx:alpine

# Remove default configuration
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Create directory for certificates (will be mounted at runtime)
RUN mkdir -p /etc/certs

# Expose HTTP and HTTPS ports
EXPOSE 80 443

# Start nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
