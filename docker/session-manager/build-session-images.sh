echo "Building RStudio session image"
docker build -t hs-rstudio-session -f hs-rstudio-session/Dockerfile .

echo "Building Jupyter session image"
docker build -t hs-jupyter-session -f hs-jupyter-session/Dockerfile .
