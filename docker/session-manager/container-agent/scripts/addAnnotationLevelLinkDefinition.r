library(emuR)
dbPath = file.path(Sys.getenv("PROJECT_PATH"), "Data", "humlabspeech_emuDB")
dbHandle = load_emuDB(dbPath)
add_linkDefinition(dbHandle, type = Sys.getenv("ANNOT_LEVEL_LINK_DEF_TYPE"), superlevelName = Sys.getenv("ANNOT_LEVEL_LINK_SUPER"), sublevelName = Sys.getenv("ANNOT_LEVEL_LINK_SUB"))
