#!/bin/bash

# Build the RStudio session image. MATLAB runtime is no longer required.
echo "Building RStudio session image"
docker build -t visp-rstudio-session --network=host -f rstudio-session/Dockerfile .
