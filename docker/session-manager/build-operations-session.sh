#!/bin/bash

#echo "Building Operations session image"
docker build -t visp-operations-session --network=host -f operations-session/Dockerfile .

