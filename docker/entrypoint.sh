#!/bin/sh
set -e

# Ensure directories exist in mounted volumes
CONF_PATH="${CN_CONF_DIR:-/app/conf}"
DATA_PATH="${CN_DATA_DIR:-/app/data}"

mkdir -p "$CONF_PATH" \
         "${CN_EPUB_DIR:-$DATA_PATH/epub}" \
         "${CN_RAW_DIR:-$DATA_PATH/raw}" \
         "${CN_COVERS_DIR:-$DATA_PATH/covers}"

# Execute command
exec "$@"
