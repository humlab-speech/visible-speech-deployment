#!/bin/bash

if  [ ! -f ./matlab_runtime/matlab_runtime_install.zip ]; then
  echo "MATLAB Runtime not found, downloading..."
  if  [ ! -d ./matlab_runtime ]; then
    mkdir ./matlab_runtime
  fi

  wget -O ./matlab_runtime/matlab_runtime_install.zip https://ssd.mathworks.com/supportfiles/downloads/R2022a/Release/0/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2022a_glnxa64.zip
fi

#echo "Building RStudio session image"
docker build -t visp-rstudio-session --network=host -f rstudio-session/Dockerfile .

