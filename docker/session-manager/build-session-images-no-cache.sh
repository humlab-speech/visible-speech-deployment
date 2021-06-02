cp -Rvp ../../container-agent/dist ./container-agent

echo "Building Operations session image"
docker build --no-cache -t hs-operations-session -f hs-operations-session/Dockerfile .

echo "Building RStudio session image"
docker build --no-cache -t hs-rstudio-session -f hs-rstudio-session/Dockerfile .

echo "Building Jupyter session image"
docker build --no-cache -t hs-jupyter-session -f hs-jupyter-session/Dockerfile .

rm -R ./container-agent
