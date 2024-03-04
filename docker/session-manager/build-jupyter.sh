#!/bin/bash

#echo "Building Jupyter session image"
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .

