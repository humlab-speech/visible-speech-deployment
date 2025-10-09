#!/bin/bash

# Define source and target directories
SOURCE_DIR="/whisper_models/Whisper/faster-whisper"
TARGET_DIR="${HF_HOME:-$HOME/.cache/huggingface}/hub"
LOG_FILE="$HOME/whisperx_link_script.log"

# Ensure the log file exists and is writable
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"

# Function to log messages
log_message() {
    local message="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $message" | tee -a "$LOG_FILE"
}

# Ensure the target directory exists
if mkdir -p "$TARGET_DIR"; then
    log_message "Ensured target directory exists: $TARGET_DIR"
else
    log_message "ERROR: Failed to create target directory: $TARGET_DIR"
    exit 1
fi

# Check if source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    log_message "ERROR: Source directory does not exist: $SOURCE_DIR"
    exit 1
fi

# Loop through all "models--*" directories in the source directory
for model_dir in "$SOURCE_DIR"/models--*; do
    # Ensure it's a directory
    if [ -d "$model_dir" ]; then
        # Extract the model name
        model_name=$(basename "$model_dir")

        # Define the symlink path inside the Hugging Face cache
        symlink_path="$TARGET_DIR/$model_name"

        # Check if the symlink already exists
        if [ -L "$symlink_path" ]; then
            log_message "Symlink already exists: $symlink_path"
        elif [ -e "$symlink_path" ]; then
            log_message "WARNING: A file or directory already exists at: $symlink_path, skipping..."
        else
            # Create the symlink
            if ln -s "$model_dir" "$symlink_path"; then
                log_message "Created symlink: $symlink_path -> $model_dir"
            else
                log_message "ERROR: Failed to create symlink: $symlink_path -> $model_dir"
            fi
        fi
    else
        log_message "WARNING: Skipping non-directory entry: $model_dir"
    fi
done

log_message "All models linked successfully!"
exit 0
