cd ../../container-agent && npm run build && cd ../docker/session-manager
cp -Rvp ../../container-agent/dist ./container-agent

echo "Building Operations session image"
docker build -t hs-operations-session -f hs-operations-session/Dockerfile .

echo "Building RStudio session image"
docker build -t hs-rstudio-session -f hs-rstudio-session/Dockerfile .

echo "Building Jupyter session image"
docker build -t hs-jupyter-session -f hs-jupyter-session/Dockerfile .

rm -R ./container-agent
