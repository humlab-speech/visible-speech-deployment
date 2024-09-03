#!/bin/bash

# Get the directory path from the user
dir_path=$1

# Check if directory exists
if [ ! -d "$dir_path" ]; then
    echo "Directory does not exist"
    exit 1
fi

# Change to the directory
cd $dir_path

# Find all zip files and unzip them
for file in *.zip
do
    unzip $file
done

# Create a file to store the export commands
echo "#!/bin/bash" > setenv.sh

# Find all directories and make the relevant files executable
for dir in *standaloneApplication
do
    chmod +x ./$dir/run_*.sh
    chmod +x ./$dir/*
    echo "export PATH=\"/matlab_install/$dir/:\${PATH}\"" >> setenv.sh
done

# Make the setenv.sh script executable
chmod +x setenv.sh
