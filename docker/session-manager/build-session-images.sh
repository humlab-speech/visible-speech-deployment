mkdir -p ./scripts/git-agent
cp -Rvp ../../git-agent/dist/* ./scripts/git-agent/

echo "Building RStudio session image"
docker build -t hs-rstudio-session -f hs-rstudio-session/Dockerfile .

echo "Building Jupyter session image"
docker build -t hs-jupyter-session -f hs-jupyter-session/Dockerfile .

echo "Building Operations session image"
docker build -t hs-operations-session -f hs-operations-session/Dockerfile .
