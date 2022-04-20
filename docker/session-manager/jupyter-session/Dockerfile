FROM jupyter/datascience-notebook:r-4.1.3

LABEL maintainer="Johan von Boer <johan.von.boer@umu.se>"
LABEL description="The Jupyter session image, which is instantiated when a new Jupyter session is spawned in the VISP interface"

USER root

RUN rm /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Stockholm /etc/localtime

RUN apt-get update
RUN apt-get install -y zlib1g-dev nano curl libnode64 libnode-dev r-cran-v8

#Not sure why I have to manually link in libv8 like this, but ld fails otherwise when trying to build V8 (which is a dep of emuR)
WORKDIR /opt/conda/lib
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8.so ./
RUN ln -s /usr/lib/x86_64-linux-gnu/libv8_libplatform.so ./

WORKDIR /home/jovyan

RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("git2r")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("openxlsx")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("ggpubr")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("gt")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("tidyverse")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("tidymodels")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("devtools")' | R --save
RUN echo 'chooseCRANmirror(graphics=FALSE, ind=61);install.packages("emuR")' | R --save

#RUN echo 'devtools::install_github("tjmahr/tjm.praat")' | R --save
#RUN echo 'devtools::install_github("humlab-speech/superassp",dependencies = "Imports")' | R --save
#RUN echo 'devtools::install_github("humlab-speech/reindeer",dependencies = "Imports")' | R --save
##RUN echo 'devtools::install_github("humlab-speech/eloquent.researcher",dependencies = "Imports")' | R --save
#RUN echo 'devtools::install_github("johanvonboer/eloquent.researcher",dependencies = "Imports")' | R --save

RUN echo 'library(devtools); install_github("IPS-LMU/wrassp",dependencies = "Imports")' | R --save
RUN echo 'library(devtools); install_github("tjmahr/tjm.praat")' | R --save
RUN echo 'library(devtools); install_github("humlab-speech/superassp",dependencies = "Imports")' | R --save
RUN echo 'devtools::install_github("johanvonboer/eloquent.researcher",dependencies = "Imports")' | R --save

RUN pip install --upgrade pip
RUN pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install https://github.com/pyannote/pyannote-audio/archive/develop.zip
RUN pip install speechbrain

RUN mkdir /matlab_install

WORKDIR /matlab_install

# Note: If we change MATLAB runtime version, we will also need to change the directory.
RUN wget -O matlab_runtime_install.zip https://ssd.mathworks.com/supportfiles/downloads/R2022a/Release/0/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2022a_glnxa64.zip
ENV MATLAB_RUNTIME_DIRECTORY=/usr/local/MATLAB/MATLAB_Runtime/v912/

RUN unzip matlab_runtime_install.zip
RUN rm matlab_runtime_install.zip
RUN ./install -mode silent -agreeToLicense yes
RUN rm -rf /matlab_install/*

COPY ./matlab-scripts/voice_analysisstandaloneApplication.zip .
RUN unzip voice_analysisstandaloneApplication.zip
RUN chmod +x ./voice_analysisstandaloneApplication/run_voice_analysis.sh
RUN chmod +x ./voice_analysisstandaloneApplication/voice_analysis
ENV PATH="/matlab_install/voice_analysisstandaloneApplication/:${PATH}"

WORKDIR /home/jovyan

#Always force the next cmds, stolen from https://stackoverflow.com/questions/35134713/disable-cache-for-specific-run-commands
ADD "https://www.random.org/cgi-bin/randbyte?nbytes=10&format=h" skipcache

RUN echo 'library(devtools); install_github("humlab-speech/reindeer", dependencies = TRUE)' | R --save

WORKDIR /

COPY ./praat/praat.tgz /praat.tgz
RUN tar xzf praat.tgz && mv praat_nogui /usr/bin/praat_nogui && ln -s /usr/bin/praat_nogui /usr/bin/praat

COPY ./container-agent /container-agent
COPY ./files/jupyter_notebook_config.py /home/jovyan/.jupyter/jupyter_notebook_config.py

RUN mkdir /home/jovyan/project
