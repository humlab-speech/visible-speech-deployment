library(git2r)
repo <- clone(Sys.getenv("GIT_REPOSITORY_URL"), Sys.getenv("PROJECT_PATH"))
