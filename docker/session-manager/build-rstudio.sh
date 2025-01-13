#!/bin/bash

if [ ! -f ./matlab_runtime_installer/current_matlab_install ]; then
  echo "MATLAB Runtime not found, running download script..."
  ./matlab-scripts/download_matlab_runtime_environment.sh
fi

#echo "Building RStudio session image"
docker build -t visp-rstudio-session --network=host -f rstudio-session/Dockerfile .

