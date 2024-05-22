#echo "Building Operations session image"
docker build -t visp-operations-session -f operations-session/Dockerfile .

#echo "Building RStudio session image"
docker build -t visp-rstudio-session -f rstudio-session/Dockerfile .

#echo "Building Jupyter session image"
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .

