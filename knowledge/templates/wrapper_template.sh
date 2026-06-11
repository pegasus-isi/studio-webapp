#!/bin/bash
#
# [CUSTOMIZE] Description of what this wrapper does.
#
# Shell wrappers are useful when:
# - The tool produces nested output directories that need flattening
# - The tool requires headless/display handling (FastQC, QUAST)
# - Scripts are already embedded in the container image
#
# References:
#   - mag-workflow/bin/megahit.sh — output flattening pattern
#   - references/PEGASUS.md "Shell Wrapper Scripts" section
#
# This wrapper is registered in the Transformation Catalog. Its arguments
# must match what workflow_generator.py passes in add_args().
#

set -euo pipefail

echo "=== [CUSTOMIZE] Step Name ==="
echo "Arguments: $@"
echo "Date: $(date)"

# ------------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------------
OUTPUT_DIR=""
SAMPLE=""
ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--out-dir)
            OUTPUT_DIR="$2"
            ARGS+=("$1" "$2")
            shift 2
            ;;
        --sample)
            SAMPLE="$2"
            shift 2
            ;;
        # [CUSTOMIZE] Add more argument parsing as needed
        # --threads)
        #     THREADS="$2"
        #     shift 2
        #     ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

# ------------------------------------------------------------------
# [CUSTOMIZE] Headless mode for GUI tools (FastQC, QUAST)
# ------------------------------------------------------------------
# unset DISPLAY
# export JAVA_TOOL_OPTIONS="-Djava.awt.headless=true"
#
# if command -v xvfb-run &> /dev/null; then
#     xvfb-run --auto-servernum mytool "${ARGS[@]}"
# else
#     mytool "${ARGS[@]}"
# fi

# ------------------------------------------------------------------
# Run the tool
# ------------------------------------------------------------------
echo "Running: mytool ${ARGS[*]}"
mytool "${ARGS[@]}"  # [CUSTOMIZE] Replace with actual tool

# ------------------------------------------------------------------
# Flatten output for Pegasus stage-out
# ------------------------------------------------------------------
# Tools that create nested directories (MEGAHIT, QUAST, Prokka, GTDB-Tk)
# need their outputs copied to the working directory root where Pegasus
# expects them based on the File() declarations in workflow_generator.py.

if [[ -n "$OUTPUT_DIR" ]]; then
    # [CUSTOMIZE] Copy the specific output files Pegasus expects.
    #
    # MEGAHIT pattern:
    #   cp "${OUTPUT_DIR}/final.contigs.fa" "${SAMPLE}_contigs.fa"
    #
    # QUAST pattern:
    #   cp "${OUTPUT_DIR}/report.tsv" "${SAMPLE}_quast_report.tsv"
    #   cp "${OUTPUT_DIR}/report.html" "${SAMPLE}_quast_report.html"
    #
    # GTDB-Tk pattern:
    #   find "${OUTPUT_DIR}" -name "*.summary.tsv" -exec cp {} "${SAMPLE}_taxonomy.tsv" \;
    #
    # Prokka pattern:
    #   cp "${OUTPUT_DIR}/${PREFIX}.gff" "${SAMPLE}_annotation.gff"
    #   cp "${OUTPUT_DIR}/${PREFIX}.faa" "${SAMPLE}_proteins.faa"

    echo "[CUSTOMIZE] Copy outputs from ${OUTPUT_DIR} to working directory"
fi

echo "Completed successfully"
