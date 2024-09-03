#!/bin/bash

# Function to set the MATLAB_RUNTIME_DIRECTORY environment variable
set_matlab_runtime_directory() {
    echo "Setting MATLAB_RUNTIME_DIRECTORY..."

    # Get the base MATLAB directory
    base_dir="/usr/local/MATLAB/MATLAB_Runtime/"

    # Find the latest version
    latest_version=$(ls -v $base_dir | tail -n 1)

    # Set the MATLAB_RUNTIME_DIRECTORY environment variable
    export MATLAB_RUNTIME_DIRECTORY="${base_dir}${latest_version}/"

    # Add the MATLAB_RUNTIME_DIRECTORY environment variable to the .bashrc and .bash_profile files for the jovyan user
    echo "export MATLAB_RUNTIME_DIRECTORY=${MATLAB_RUNTIME_DIRECTORY}" >> /home/jovyan/.bashrc
    echo "export MATLAB_RUNTIME_DIRECTORY=${MATLAB_RUNTIME_DIRECTORY}" >> /home/jovyan/.bash_profile

    echo "MATLAB_RUNTIME_DIRECTORY set to ${MATLAB_RUNTIME_DIRECTORY}"
}

# Function to verify that the MATLAB_RUNTIME_DIRECTORY environment variable has been properly set
verify_matlab_runtime_directory() {
    echo "Verifying MATLAB_RUNTIME_DIRECTORY..."

    # Get the base MATLAB directory
    base_dir="/usr/local/MATLAB/MATLAB_Runtime/"

    # Find the latest version
    latest_version=$(ls -v $base_dir | tail -n 1)

    # Check if the MATLAB_RUNTIME_DIRECTORY environment variable has been set
    if [ -z "${MATLAB_RUNTIME_DIRECTORY}" ]; then
        echo "MATLAB_RUNTIME_DIRECTORY is not set."
        exit 1
    fi

    echo "MATLAB_RUNTIME_DIRECTORY is set to ${MATLAB_RUNTIME_DIRECTORY}"

    # Check if the directory exists
    if [ ! -d "${MATLAB_RUNTIME_DIRECTORY}" ]; then
        echo "The directory ${MATLAB_RUNTIME_DIRECTORY} does not exist."
        exit 1
    fi

    echo "The directory ${MATLAB_RUNTIME_DIRECTORY} exists"

    # Check if the MATLAB_RUNTIME_DIRECTORY environment variable points to the latest version
    if [ "${MATLAB_RUNTIME_DIRECTORY}" != "${base_dir}${latest_version}/" ]; then
        echo "MATLAB_RUNTIME_DIRECTORY does not point to the latest MATLAB runtime version."
        exit 1
    fi

    echo "MATLAB_RUNTIME_DIRECTORY points to the latest MATLAB runtime version."
}

# Get the user argument
user_arg=$1

# Check the user argument and call the appropriate function
if [ "$user_arg" = "set" ]; then
    set_matlab_runtime_directory
elif [ "$user_arg" = "verify" ] || [ -z "$user_arg" ]; then
    verify_matlab_runtime_directory
else
    echo "Invalid argument. Please use 'set' to set the MATLAB_RUNTIME_DIRECTORY environment variable, or 'verify' to verify that it has been properly set."
    exit 1
fi
