#!/bin/bash
#
# MEGAHIT wrapper script for MAG workflow
# Performs metagenomic assembly
#

set -euo pipefail

echo "=== MEGAHIT Metagenome Assembly ==="
echo "Arguments: $@"
echo "Date: $(date)"

# Parse arguments to extract output directory
OUTPUT_DIR=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--out-dir)
            OUTPUT_DIR="$2"
            ARGS+=("$1" "$2")
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

# Create a deterministic assembly log for Pegasus outputs
SAMPLE_NAME="assembly"
if [[ -n "$OUTPUT_DIR" ]]; then
    SAMPLE_NAME=$(basename "$OUTPUT_DIR" | sed 's/_megahit$//')
fi
LOG_FILE="${SAMPLE_NAME}_assembly.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

# Run MEGAHIT
megahit "${ARGS[@]}"

# Copy contigs to expected output location
if [[ -n "$OUTPUT_DIR" && -f "${OUTPUT_DIR}/final.contigs.fa" ]]; then
    cp "${OUTPUT_DIR}/final.contigs.fa" "${SAMPLE_NAME}_contigs.fa"
    echo "Contigs copied to ${SAMPLE_NAME}_contigs.fa"
fi

echo "MEGAHIT completed successfully"
