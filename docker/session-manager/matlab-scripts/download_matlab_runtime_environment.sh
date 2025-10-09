#!/bin/bash

# MATLAB Runtime download URL
#url="https://ssd.mathworks.com/supportfiles/downloads/R2022b/Release/5/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2022b_Update_5_glnxa64.zip"
#url="https://ssd.mathworks.com/supportfiles/downloads/R2023a/Release/5/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2023a_Update_5_glnxa64.zip"
url="https://ssd.mathworks.com/supportfiles/downloads/R2023b/Release/4/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2023b_Update_4_glnxa64.zip"

# Subdirectory name
dir="matlab_runtime_installer"

# Get the filename from the URL
filename=$(basename "$url")

# Parse the MATLAB version from the URL
version=$(echo $url | grep -oP 'R\d{4}[ab]' | sed 's/R//' | head -1)

# Function to verify the hashes of the downloaded file
verify_hashes() {
    file=$1
    old_md5=$2
    old_sha1=$3
    old_sha256=$4

    # Calculate file hashes
    md5=$(md5sum "$file" | awk '{ print $1 }')
    sha1=$(sha1sum "$file" | awk '{ print $1 }')
    sha256=$(sha256sum "$file" | awk '{ print $1 }')

    # Compare the old and new hashes
    if [ "$old_md5" == "$md5" ] && [ "$old_sha1" == "$sha1" ] && [ "$old_sha256" == "$sha256" ]; then
        echo "The file matches the hashes in the details file."
    else
        echo "The file does not match the hashes in the details file."
    fi
}

# Check if the file already exists
if [ -f "$dir/$version/$filename" ]; then
    echo "Warning: File already exists."

    # Display the contents of the details text file
    cat "$dir/$version/matlab_runtime_details.txt"

    # Check if the download location is the same
    old_url=$(grep -oP '(?<=URL: ).*' "$dir/$version/matlab_runtime_details.txt")
    if [ "$old_url" == "$url" ]; then
        echo "The download location is the same."
    else
        echo "The download location has changed."
    fi

    # Store old hashes
    old_md5=$(grep -oP '(?<=MD5: ).*' "$dir/$version/matlab_runtime_details.txt")
    old_sha1=$(grep -oP '(?<=SHA1: ).*' "$dir/$version/matlab_runtime_details.txt")
    old_sha256=$(grep -oP '(?<=SHA256: ).*' "$dir/$version/matlab_runtime_details.txt")

    # Verify the hashes of the downloaded file
    verify_hashes "$dir/$version/$filename" "$old_md5" "$old_sha1" "$old_sha256"

    # Check if the script was run with the 'overwrite' argument
    if [ "$1" != "overwrite" ]; then
        echo "To overwrite the existing file, run the script with the 'overwrite' argument."
        # Still create the symlink if file exists
    fi
else
    # Create the directory if it doesn't exist
    mkdir -p "$dir/$version"

    # Download the MATLAB Runtime
    wget -O "$dir/$version/$filename" $url

    # Calculate file hashes
    md5=$(md5sum "$dir/$version/$filename" | awk '{ print $1 }')
    sha1=$(sha1sum "$dir/$version/$filename" | awk '{ print $1 }')
    sha256=$(sha256sum "$dir/$version/$filename" | awk '{ print $1 }')

    # Save details to a text file
    echo "URL: $url" > "$dir/$version/matlab_runtime_details.txt"
    echo "MD5: $md5" >> "$dir/$version/matlab_runtime_details.txt"
    echo "SHA1: $sha1" >> "$dir/$version/matlab_runtime_details.txt"
    echo "SHA256: $sha256" >> "$dir/$version/matlab_runtime_details.txt"
fi

# Create or update the symbolic link
ln -f "$(pwd)/$dir/$version/$filename" "$(pwd)/$dir/current_matlab_install"
