library(emuR)
dbPath = file.path(Sys.getenv("PROJECT_PATH"), "Data", "humlabspeech_emuDB")
dbHandle = load_emuDB(dbPath)