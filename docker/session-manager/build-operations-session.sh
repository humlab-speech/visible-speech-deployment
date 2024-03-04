#!/bin/bash

#echo "Building Operations session image"
docker build -t visp-operations-session -f operations-session/Dockerfile .

