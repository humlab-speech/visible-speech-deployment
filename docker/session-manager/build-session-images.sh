#!/bin/bash

#echo "Building Operations session image"
docker build -t visp-operations-session -f operations-session/Dockerfile .

# MATLAB runtime no longer required for session-manager builds.
# Previous versions attempted to download and install MATLAB runtime here.
# That logic has been removed because MATLAB is not needed anymore.

#echo "Building RStudio session image"
docker build -t visp-rstudio-session -f rstudio-session/Dockerfile .

#echo "Building Jupyter session image"
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .
