library(emuR);

dbPath = file.path("/home/rstudio", "default_emuDB")
dbHandle = load_emuDB(dbPath)

wavDir = file.path("/home/rstudio/", "uploads")

sessionDirs = list.files(wavDir);

for(sessDir in sessionDirs) {
  wavDir = file.path("/home/rstudio/uploads", sessDir);
  import_mediaFiles(dbHandle, dir = wavDir, targetSessionName = sessDir)
}

