#!/bin/bash

#echo "Building Operations session image"
cp -Rp ../../container-agent/* ./files/container-agent/
docker build -t visp-operations-session --network=host -f operations-session/Dockerfile .

