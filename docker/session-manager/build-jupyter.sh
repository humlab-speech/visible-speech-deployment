#!/bin/bash

#echo "Building Jupyter session image"
cp -Rp ../../container-agent/* ./files/container-agent/
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .

