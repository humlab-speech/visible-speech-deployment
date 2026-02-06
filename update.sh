#!/bin/bash

# Update script for VISP deployment

set -e

echo "Updating VISP components..."

# Run the Python deployment script
python3 visp-deploy.py update

echo "Update complete."
