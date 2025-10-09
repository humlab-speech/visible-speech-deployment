#!/bin/bash

#echo "Building Operations session image"
podman build -t visp-operations-session -f operations-session/Dockerfile .

if [ -f ./matlab-scripts/download_matlab_runtime_environment.sh ]; then
  if [ ! -L ./matlab_runtime_installer/current_matlab_install ]; then
    echo "MATLAB Runtime symlink not found, running download script..."
    ./matlab-scripts/download_matlab_runtime_environment.sh
    if [ ! -L ./matlab_runtime_installer/current_matlab_install ]; then
      echo "Failed to set up MATLAB Runtime. Exiting."
      exit 1
    fi
  fi
else
  echo "MATLAB download script not found. MATLAB is required. Exiting."
  exit 1
fi

#echo "Building RStudio session image"
podman build -t visp-rstudio-session -f rstudio-session/Dockerfile .

#echo "Building Jupyter session image"
podman build -t visp-jupyter-session -f jupyter-session/Dockerfile .
