cd ../../container-agent && npm run build && cd ../docker/session-manager && cp -Rvp ../../container-agent/dist ./container-agent

#echo "Building Operations session image"
docker build -t visp-operations-session -f operations-session/Dockerfile .

#echo "Building RStudio session image"
#docker build -t visp-rstudio-session -f rstudio-session/Dockerfile .

#echo "Building Jupyter session image"
#docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .

#echo "Building VScode session image"
#docker build -t visp-vscode-session -f vscode-session/Dockerfile .

rm -R ./container-agent
