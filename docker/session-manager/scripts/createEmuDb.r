library(emuR)
dbPath = file.path(Sys.getenv("PROJECT_PATH"), "Data")
create_emuDB(name='humlabspeech', targetDir = dbPath)
