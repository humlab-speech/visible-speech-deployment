cp -Rvp ../../container-agent/dist ./container-agent

echo "Building Operations session image"
docker build --no-cache -t visp-operations-session -f operations-session/Dockerfile .

echo "Building RStudio session image"
docker build --no-cache -t visp-rstudio-session -f rstudio-session/Dockerfile .

echo "Building Jupyter session image"
docker build --no-cache -t visp-jupyter-session -f jupyter-session/Dockerfile .

rm -R ./container-agent
