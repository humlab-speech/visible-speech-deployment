#!/bin/bash
set -e

# Initial build
echo "Building application..."
rm -rf dist/*
NODE_OPTIONS=--openssl-legacy-provider npx webpack --config webpack.prod.js

# Start nginx in background
echo "Starting nginx..."
nginx

# Start webpack in watch mode
echo "Starting webpack watch mode..."
NODE_OPTIONS=--openssl-legacy-provider npx webpack --config webpack.prod.js --watch
