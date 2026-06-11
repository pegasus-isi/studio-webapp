#!/bin/bash
#
# Manual test script for MAG workflow
# Tests each step of the pipeline before running with Pegasus
#
# Usage:
#   ./test_workflow_manual.sh [--use-docker] [--skip-download]
#
# Options:
#   --use-docker    Run tools inside Docker container
#   --skip-download Skip downloading test data (if already downloaded)
#   --skip-binning  Skip binning and downstream steps (faster test)
#   --skip-fastqc   Skip FastQC (fastp provides QC metrics anyway)
#   --threads N     Number of threads to use (default: 4)
#
# Note: GPUs don't help with this workflow - FastQC, fastp, MEGAHIT, etc.
#       are all CPU-based tools. More CPU cores and faster storage help most.
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DATA_DIR="${SCRIPT_DIR}/test_data"
OUTPUT_DIR="${SCRIPT_DIR}/test_output"
CONTAINER_IMAGE="kthare10/mag-workflow:latest"

# Test data URLs (nf-core/mag test data)
TEST_DATA_BASE_URL="https://github.com/nf-core/test-datasets/raw/mag/test_data"
SAMPLE_NAME="test_minigut"

# Parse arguments
USE_DOCKER=false
SKIP_DOWNLOAD=false
SKIP_BINNING=false
SKIP_FASTQC=false
THREADS=4

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
        --skip-binning)
            SKIP_BINNING=true
            shift
            ;;
        --skip-fastqc)
            SKIP_FASTQC=true
            shift
            ;;
        --threads)
            THREADS="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--use-docker] [--skip-download] [--skip-binning] [--skip-fastqc] [--threads N]"
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}STEP: $1${NC}"
    echo -e "${GREEN}========================================${NC}"
}

# Function to run command (with or without Docker)
run_cmd() {
    if [ "$USE_DOCKER" = true ]; then
        docker run --rm \
            -v "${TEST_DATA_DIR}:/data" \
            -v "${OUTPUT_DIR}:/output" \
            -w /output \
            -e JAVA_TOOL_OPTIONS="-Djava.awt.headless=true" \
            "${CONTAINER_IMAGE}" \
            "$@"
    else
        "$@"
    fi
}

# Function to run command with xvfb (for FastQC)
run_cmd_xvfb() {
    if [ "$USE_DOCKER" = true ]; then
        docker run --rm \
            -v "${TEST_DATA_DIR}:/data" \
            -v "${OUTPUT_DIR}:/output" \
            -w /output \
            -e JAVA_TOOL_OPTIONS="-Djava.awt.headless=true" \
            "${CONTAINER_IMAGE}" \
            xvfb-run --auto-servernum "$@"
    else
        if command -v xvfb-run &> /dev/null; then
            xvfb-run --auto-servernum "$@"
        else
            JAVA_TOOL_OPTIONS="-Djava.awt.headless=true" "$@"
        fi
    fi
}

echo ""
echo "=============================================="
echo "  MAG Workflow Manual Test Script"
echo "=============================================="
echo ""
echo "Configuration:"
echo "  Use Docker:    ${USE_DOCKER}"
echo "  Skip Download: ${SKIP_DOWNLOAD}"
echo "  Skip Binning:  ${SKIP_BINNING}"
echo "  Skip FastQC:   ${SKIP_FASTQC}"
echo "  Threads:       ${THREADS}"
echo "  Test Data Dir: ${TEST_DATA_DIR}"
echo "  Output Dir:    ${OUTPUT_DIR}"
echo ""
echo "Note: GPUs don't accelerate this workflow. Use more CPU cores (--threads)"
echo "      and fast SSD storage for best performance."
echo ""

# Create directories
mkdir -p "${TEST_DATA_DIR}"
mkdir -p "${OUTPUT_DIR}"

# ============================================================
# Step 0: Download test data
# ============================================================
log_step "Downloading test data"

if [ "$SKIP_DOWNLOAD" = false ]; then
    R1_FILE="${TEST_DATA_DIR}/${SAMPLE_NAME}_R1.fastq.gz"
    R2_FILE="${TEST_DATA_DIR}/${SAMPLE_NAME}_R2.fastq.gz"

    if [ ! -f "$R1_FILE" ]; then
        log_info "Downloading ${SAMPLE_NAME}_R1.fastq.gz..."
        curl -L -o "$R1_FILE" "${TEST_DATA_BASE_URL}/${SAMPLE_NAME}_R1.fastq.gz"
    else
        log_info "R1 file already exists, skipping download"
    fi

    if [ ! -f "$R2_FILE" ]; then
        log_info "Downloading ${SAMPLE_NAME}_R2.fastq.gz..."
        curl -L -o "$R2_FILE" "${TEST_DATA_BASE_URL}/${SAMPLE_NAME}_R2.fastq.gz"
    else
        log_info "R2 file already exists, skipping download"
    fi

    log_success "Test data ready"
else
    log_info "Skipping download (--skip-download)"
fi

# Verify test data exists
if [ ! -f "${TEST_DATA_DIR}/${SAMPLE_NAME}_R1.fastq.gz" ] || \
   [ ! -f "${TEST_DATA_DIR}/${SAMPLE_NAME}_R2.fastq.gz" ]; then
    log_error "Test data files not found!"
    exit 1
fi

log_info "Test data files:"
ls -lh "${TEST_DATA_DIR}"/*.fastq.gz

# ============================================================
# Step 1: FastQC - Quality Control (Optional)
# ============================================================
log_step "1. FastQC - Quality Control"

cd "${OUTPUT_DIR}"

if [ "$SKIP_FASTQC" = true ]; then
    log_warning "Skipping FastQC (--skip-fastqc). fastp will provide QC metrics."
else
    log_info "Running FastQC on raw reads (this can be slow)..."
    log_info "Tip: Use --skip-fastqc since fastp also provides QC metrics"
    if [ "$USE_DOCKER" = true ]; then
        run_cmd_xvfb fastqc \
            /data/${SAMPLE_NAME}_R1.fastq.gz \
            /data/${SAMPLE_NAME}_R2.fastq.gz \
            --outdir /output \
            --threads ${THREADS}
    else
        run_cmd_xvfb fastqc \
            "${TEST_DATA_DIR}/${SAMPLE_NAME}_R1.fastq.gz" \
            "${TEST_DATA_DIR}/${SAMPLE_NAME}_R2.fastq.gz" \
            --outdir "${OUTPUT_DIR}" \
            --threads ${THREADS}
    fi

    if [ -f "${OUTPUT_DIR}/${SAMPLE_NAME}_R1_fastqc.html" ]; then
        log_success "FastQC completed - HTML reports generated"
        ls -lh "${OUTPUT_DIR}"/*fastqc*
    else
        log_error "FastQC failed - no output generated"
        exit 1
    fi
fi

# ============================================================
# Step 2: fastp - Read Trimming
# ============================================================
log_step "2. fastp - Read Trimming"

log_info "Running fastp for quality trimming (also provides QC metrics)..."
if [ "$USE_DOCKER" = true ]; then
    run_cmd fastp \
        -i /data/${SAMPLE_NAME}_R1.fastq.gz \
        -I /data/${SAMPLE_NAME}_R2.fastq.gz \
        -o /output/${SAMPLE_NAME}_trimmed_R1.fastq.gz \
        -O /output/${SAMPLE_NAME}_trimmed_R2.fastq.gz \
        --json /output/${SAMPLE_NAME}_fastp.json \
        --html /output/${SAMPLE_NAME}_fastp.html \
        --thread ${THREADS} \
        --qualified_quality_phred 20 \
        --length_required 50
else
    run_cmd fastp \
        -i "${TEST_DATA_DIR}/${SAMPLE_NAME}_R1.fastq.gz" \
        -I "${TEST_DATA_DIR}/${SAMPLE_NAME}_R2.fastq.gz" \
        -o "${OUTPUT_DIR}/${SAMPLE_NAME}_trimmed_R1.fastq.gz" \
        -O "${OUTPUT_DIR}/${SAMPLE_NAME}_trimmed_R2.fastq.gz" \
        --json "${OUTPUT_DIR}/${SAMPLE_NAME}_fastp.json" \
        --html "${OUTPUT_DIR}/${SAMPLE_NAME}_fastp.html" \
        --thread ${THREADS} \
        --qualified_quality_phred 20 \
        --length_required 50
fi

if [ -f "${OUTPUT_DIR}/${SAMPLE_NAME}_trimmed_R1.fastq.gz" ]; then
    log_success "fastp completed - trimmed reads generated"
    ls -lh "${OUTPUT_DIR}"/*trimmed*.fastq.gz
else
    log_error "fastp failed - no output generated"
    exit 1
fi

# ============================================================
# Step 3: MEGAHIT - Assembly
# ============================================================
log_step "3. MEGAHIT - Metagenome Assembly"

log_info "Running MEGAHIT assembly (this is the slowest step)..."
if [ "$USE_DOCKER" = true ]; then
    # Remove existing output directory if exists (MEGAHIT requirement)
    run_cmd rm -rf /output/${SAMPLE_NAME}_megahit || true
    run_cmd megahit \
        -1 /output/${SAMPLE_NAME}_trimmed_R1.fastq.gz \
        -2 /output/${SAMPLE_NAME}_trimmed_R2.fastq.gz \
        -o /output/${SAMPLE_NAME}_megahit \
        -t ${THREADS} \
        --min-contig-len 500
    # Copy final contigs
    run_cmd cp /output/${SAMPLE_NAME}_megahit/final.contigs.fa /output/${SAMPLE_NAME}_contigs.fa
else
    rm -rf "${OUTPUT_DIR}/${SAMPLE_NAME}_megahit" || true
    run_cmd megahit \
        -1 "${OUTPUT_DIR}/${SAMPLE_NAME}_trimmed_R1.fastq.gz" \
        -2 "${OUTPUT_DIR}/${SAMPLE_NAME}_trimmed_R2.fastq.gz" \
        -o "${OUTPUT_DIR}/${SAMPLE_NAME}_megahit" \
        -t ${THREADS} \
        --min-contig-len 500
    cp "${OUTPUT_DIR}/${SAMPLE_NAME}_megahit/final.contigs.fa" "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa"
fi

if [ -f "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa" ]; then
    log_success "MEGAHIT completed - contigs generated"
    log_info "Contig stats:"
    if [ "$USE_DOCKER" = true ]; then
        run_cmd sh -c "grep -c '^>' /output/${SAMPLE_NAME}_contigs.fa || echo 0"
    else
        echo "  Number of contigs: $(grep -c '^>' "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa" || echo 0)"
    fi
    ls -lh "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa"
else
    log_error "MEGAHIT failed - no contigs generated"
    exit 1
fi

# ============================================================
# Step 4: QUAST - Assembly QC
# ============================================================
log_step "4. QUAST - Assembly Quality Assessment"

log_info "Running QUAST..."
if [ "$USE_DOCKER" = true ]; then
    run_cmd quast.py \
        /output/${SAMPLE_NAME}_contigs.fa \
        -o /output/${SAMPLE_NAME}_quast \
        --min-contig 500 \
        --threads ${THREADS}
else
    run_cmd quast.py \
        "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa" \
        -o "${OUTPUT_DIR}/${SAMPLE_NAME}_quast" \
        --min-contig 500 \
        --threads ${THREADS}
fi

if [ -f "${OUTPUT_DIR}/${SAMPLE_NAME}_quast/report.txt" ]; then
    log_success "QUAST completed"
    log_info "Assembly statistics:"
    cat "${OUTPUT_DIR}/${SAMPLE_NAME}_quast/report.txt"
else
    log_error "QUAST failed"
    exit 1
fi

# ============================================================
# Step 5: Prodigal - Gene Prediction
# ============================================================
log_step "5. Prodigal - Gene Prediction"

log_info "Running Prodigal..."
if [ "$USE_DOCKER" = true ]; then
    run_cmd prodigal \
        -i /output/${SAMPLE_NAME}_contigs.fa \
        -a /output/${SAMPLE_NAME}_genes.faa \
        -o /output/${SAMPLE_NAME}_genes.gff \
        -f gff \
        -p meta
else
    run_cmd prodigal \
        -i "${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa" \
        -a "${OUTPUT_DIR}/${SAMPLE_NAME}_genes.faa" \
        -o "${OUTPUT_DIR}/${SAMPLE_NAME}_genes.gff" \
        -f gff \
        -p meta
fi

if [ -f "${OUTPUT_DIR}/${SAMPLE_NAME}_genes.faa" ]; then
    log_success "Prodigal completed - genes predicted"
    if [ "$USE_DOCKER" = true ]; then
        run_cmd sh -c "grep -c '^>' /output/${SAMPLE_NAME}_genes.faa || echo 0"
    else
        echo "  Number of predicted genes: $(grep -c '^>' "${OUTPUT_DIR}/${SAMPLE_NAME}_genes.faa" || echo 0)"
    fi
else
    log_error "Prodigal failed"
    exit 1
fi

# ============================================================
# Optional: Binning and downstream steps
# ============================================================
if [ "$SKIP_BINNING" = true ]; then
    log_warning "Skipping binning steps (--skip-binning)"
else
    log_step "6. MetaBAT2 - Genome Binning"
    log_warning "MetaBAT2 requires BAM files from read mapping."
    log_warning "Skipping binning for this quick test."
    log_info "In the full workflow, reads are mapped back to contigs first."
fi

# ============================================================
# Step 7: MultiQC - Report Aggregation
# ============================================================
log_step "7. MultiQC - Report Aggregation"

log_info "Running MultiQC..."
if [ "$USE_DOCKER" = true ]; then
    run_cmd multiqc /output -o /output/multiqc --force
else
    run_cmd multiqc "${OUTPUT_DIR}" -o "${OUTPUT_DIR}/multiqc" --force
fi

if [ -f "${OUTPUT_DIR}/multiqc/multiqc_report.html" ]; then
    log_success "MultiQC completed - report generated"
    ls -lh "${OUTPUT_DIR}/multiqc/multiqc_report.html"
else
    log_warning "MultiQC may not have found compatible files"
fi

# ============================================================
# Summary
# ============================================================
echo ""
echo "=============================================="
echo "  TEST COMPLETED SUCCESSFULLY!"
echo "=============================================="
echo ""
echo "Output files in: ${OUTPUT_DIR}"
echo ""
ls -lh "${OUTPUT_DIR}"
echo ""
log_info "Key outputs:"
echo "  - FastQC reports:  ${OUTPUT_DIR}/${SAMPLE_NAME}_R*_fastqc.html"
echo "  - fastp report:    ${OUTPUT_DIR}/${SAMPLE_NAME}_fastp.html"
echo "  - Contigs:         ${OUTPUT_DIR}/${SAMPLE_NAME}_contigs.fa"
echo "  - QUAST report:    ${OUTPUT_DIR}/${SAMPLE_NAME}_quast/report.html"
echo "  - Predicted genes: ${OUTPUT_DIR}/${SAMPLE_NAME}_genes.faa"
echo "  - MultiQC report:  ${OUTPUT_DIR}/multiqc/multiqc_report.html"
echo ""
log_success "All core steps passed! Ready to run with Pegasus."
