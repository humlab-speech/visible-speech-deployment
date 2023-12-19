#!/bin/bash
cd ../../container-agent && npm run build && cd ../docker/session-manager && cp -Rvp ../../container-agent/dist ./container-agent

#echo "Building Jupyter session image"
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .

rm -R ./container-agent
