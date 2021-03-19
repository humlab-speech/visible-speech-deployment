library(git2r)
projectPath = file.path(Sys.getenv("PROJECT_PATH"))
repo <- repository(projectPath)
workdir(repo)
config(repo, user.name=Sys.getenv("GIT_USER_NAME"), user.email=Sys.getenv("GIT_USER_EMAIL"))
add(repo, ".")
commit(repo, "System auto commit")
push(repo, "origin", "refs/heads/master")

