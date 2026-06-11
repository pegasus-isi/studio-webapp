#!/bin/bash
#
# Manual test script for [WORKFLOW_NAME] workflow
# Tests each pipeline step locally before running with Pegasus.
#
# This validates:
# - Tool installations and dependencies
# - Argument parsing matches workflow_generator.py
# - Output files are created correctly
#
# Usage:
#   ./run_manual.sh [--use-docker] [--skip-download]
#
# References:
#   mag-workflow/run_manual.sh — comprehensive test with Docker support
#   soilmoisture-workflow/run_manual.sh — API-based pipeline test
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DATA_DIR="${SCRIPT_DIR}/test_data"
OUTPUT_DIR="${SCRIPT_DIR}/test_output"
CONTAINER_IMAGE="username/image:latest"  # [CUSTOMIZE]

# Parse arguments
USE_DOCKER=false
SKIP_DOWNLOAD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --use-docker)
            USE_DOCKER=true
            shift
            ;;
        --skip-download)
            SKIP_DOWNLOAD=true
            shift
            ;;
        # [CUSTOMIZE] Add more flags
        # --threads)
        #     THREADS="$2"
        #     shift 2
        #     ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--use-docker] [--skip-download]"
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()    { echo ""; echo -e "${GREEN}========================================${NC}"; echo -e "${GREEN}STEP: $1${NC}"; echo -e "${GREEN}========================================${NC}"; }

# Docker runner
run_cmd() {
    if [ "$USE_DOCKER" = true ]; then
        docker run --rm \
            -v "${TEST_DATA_DIR}:/data" \
            -v "${OUTPUT_DIR}:/output" \
            -w /output \
            "${CONTAINER_IMAGE}" \
            "$@"
    else
        "$@"
    fi
}

echo ""
echo "=============================================="
echo "  [CUSTOMIZE] Workflow Manual Test"
echo "=============================================="
echo ""
echo "Configuration:"
echo "  Use Docker:    ${USE_DOCKER}"
echo "  Skip Download: ${SKIP_DOWNLOAD}"
echo "  Test Data Dir: ${TEST_DATA_DIR}"
echo "  Output Dir:    ${OUTPUT_DIR}"
echo ""

mkdir -p "${TEST_DATA_DIR}" "${OUTPUT_DIR}"

# ==============================================================
# Step 0: Download / prepare test data
# ==============================================================
log_step "Preparing test data"

if [ "$SKIP_DOWNLOAD" = false ]; then
    # [CUSTOMIZE] Download or generate test data
    # Pattern A: Download from URL
    # if [ ! -f "${TEST_DATA_DIR}/test_input.csv" ]; then
    #     log_info "Downloading test data..."
    #     curl -L -o "${TEST_DATA_DIR}/test_input.csv" "https://example.com/test_data.csv"
    # fi

    # Pattern B: Generate synthetic test data
    # python3 -c "
    # import csv
    # with open('${TEST_DATA_DIR}/test_input.csv', 'w') as f:
    #     writer = csv.writer(f)
    #     writer.writerow(['id', 'value'])
    #     for i in range(10):
    #         writer.writerow([f'item_{i}', i * 1.5])
    # "
    log_info "Test data ready"
else
    log_info "Skipping download (--skip-download)"
fi

# ==============================================================
# Step 1: [CUSTOMIZE] First pipeline step
# ==============================================================
log_step "1. [Step Name]"

log_info "Running step 1..."
# [CUSTOMIZE] Run the wrapper script the same way Pegasus would:
# run_cmd python3 "${SCRIPT_DIR}/bin/step1.py" \
#     --input "${TEST_DATA_DIR}/test_input.csv" \
#     --output "${OUTPUT_DIR}/step1_output.csv"

# Verify output
# if [ -f "${OUTPUT_DIR}/step1_output.csv" ]; then
#     log_success "Step 1 completed"
#     ls -lh "${OUTPUT_DIR}/step1_output.csv"
# else
#     log_error "Step 1 failed — no output"
#     exit 1
# fi

# ==============================================================
# Step 2: [CUSTOMIZE] Second pipeline step
# ==============================================================
log_step "2. [Step Name]"

log_info "Running step 2..."
# run_cmd python3 "${SCRIPT_DIR}/bin/step2.py" \
#     --input "${OUTPUT_DIR}/step1_output.csv" \
#     --output "${OUTPUT_DIR}/step2_output.json"

# ==============================================================
# Step 3: [CUSTOMIZE] Third pipeline step
# ==============================================================
log_step "3. [Step Name]"

log_info "Running step 3..."
# run_cmd python3 "${SCRIPT_DIR}/bin/step3.py" \
#     --input "${OUTPUT_DIR}/step2_output.json" \
#     --output "${OUTPUT_DIR}/step3_result.png"

# ==============================================================
# Summary
# ==============================================================
echo ""
echo "=============================================="
echo "  TEST COMPLETED SUCCESSFULLY!"
echo "=============================================="
echo ""
echo "Output files in: ${OUTPUT_DIR}"
echo ""
ls -lh "${OUTPUT_DIR}"
echo ""
log_success "All steps passed! Ready to run with Pegasus."
