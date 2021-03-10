library(emuR);
create_emuDB(name='humlabspeech', targetDir = '/home/rstudio');

dbPath = file.path("/home/rstudio", "humlabspeech_emuDB")
dbHandle = load_emuDB(dbPath)

wavDir = file.path("/rscripts", "wavs")

import_mediaFiles(dbHandle, dir = "/wavs/", targetSessionName = 'Session 1')

bndls = list_bundles(dbHandle);

write_bundleList(dbHandle, name = "user.user", bndls);

add_levelDefinition(dbHandle, name = 'Word', type = 'ITEM')

add_levelDefinition(dbHandle, name = 'Phonetic', type = 'SEGMENT')

add_linkDefinition(dbHandle, type = 'ONE_TO_MANY', superlevelName = 'Word', sublevelName = 'Phonetic')

list_linkDefinitions(dbHandle)
