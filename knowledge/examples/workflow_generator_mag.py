#!/usr/bin/env python3

"""
MAG (Metagenome-Assembled Genomes) Workflow Generator for Pegasus WMS

This script generates a Pegasus workflow for metagenomic assembly, binning,
and annotation - equivalent to the nf-core/mag Nextflow pipeline.

Pipeline steps:
1. Quality Control (FastQC, fastp)
2. Assembly (MEGAHIT or SPAdes)
3. Assembly QC (QUAST)
4. Gene Prediction (Prodigal)
5. Binning (MetaBAT2)
6. Bin Quality Assessment (CheckM2)
7. Taxonomic Classification (GTDB-Tk)
8. Genome Annotation (Prokka)
9. Report Generation (MultiQC)

Usage:
    ./workflow_generator.py --samplesheet samples.csv --output workflow.yml
"""

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Pegasus imports
try:
    from Pegasus.api import (
        Job, File, Directory, FileServer, Site, SiteCatalog,
        Transformation, TransformationCatalog, Container,
        ReplicaCatalog, Workflow, Operation
    )
except ImportError:
    print("Error: Pegasus Python API not found.")
    print("Install with: pip install pegasus-wms.api")
    sys.exit(1)


# Default container image
DEFAULT_CONTAINER = "docker://kthare10/mag-workflow:latest"

# Test data configuration
TEST_DATA_BASE_URL = "https://github.com/nf-core/test-datasets/raw/mag/test_data"
TEST_SAMPLES = [
    {
        "sample": "test_minigut",
        "group": "minigut",
        "fastq_1": f"{TEST_DATA_BASE_URL}/test_minigut_R1.fastq.gz",
        "fastq_2": f"{TEST_DATA_BASE_URL}/test_minigut_R2.fastq.gz",
    },
    {
        "sample": "test_minigut_sample2",
        "group": "minigut",
        "fastq_1": f"{TEST_DATA_BASE_URL}/test_minigut_sample2_R1.fastq.gz",
        "fastq_2": f"{TEST_DATA_BASE_URL}/test_minigut_sample2_R2.fastq.gz",
    },
]

# Tool configurations
TOOL_CONFIGS = {
    "fastqc": {"memory": "2GB", "cores": 2},
    "fastp": {"memory": "4GB", "cores": 4},
    "megahit": {"memory": "16GB", "cores": 8},
    "spades": {"memory": "32GB", "cores": 16},
    "quast": {"memory": "4GB", "cores": 4},
    "prodigal": {"memory": "4GB", "cores": 1},
    "metabat2": {"memory": "8GB", "cores": 4},
    "checkm2": {"memory": "16GB", "cores": 8},
    "gtdbtk": {"memory": "64GB", "cores": 8},
    "prokka": {"memory": "8GB", "cores": 4},
    "multiqc": {"memory": "4GB", "cores": 2},
}


def parse_samplesheet(samplesheet_path: str) -> List[Dict]:
    """
    Parse input samplesheet CSV.

    Expected format:
    sample,fastq_1,fastq_2,group
    sample1,/path/to/sample1_R1.fastq.gz,/path/to/sample1_R2.fastq.gz,group1
    """
    samples = []
    with open(samplesheet_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sample = {
                'id': row.get('sample', row.get('id', '')),
                'fastq_1': row.get('fastq_1', row.get('R1', '')),
                'fastq_2': row.get('fastq_2', row.get('R2', '')),
                'group': row.get('group', 'default'),
                'single_end': row.get('single_end', 'false').lower() == 'true'
            }
            if sample['id'] and sample['fastq_1']:
                samples.append(sample)
    return samples


def download_test_data(output_dir: str) -> Tuple[List[Dict], str]:
    """
    Download nf-core/mag test data and generate a samplesheet.

    Args:
        output_dir: Directory to download test data to

    Returns:
        Tuple of (samples list, samplesheet path)
    """
    import urllib.request

    test_data_dir = os.path.join(output_dir, "test_data")
    os.makedirs(test_data_dir, exist_ok=True)

    print(f"Downloading nf-core/mag test data to: {test_data_dir}")
    print("-" * 60)

    downloaded_samples = []

    for sample in TEST_SAMPLES:
        sample_name = sample["sample"]
        print(f"\nSample: {sample_name}")

        # Download R1
        r1_filename = os.path.basename(sample["fastq_1"])
        r1_path = os.path.join(test_data_dir, r1_filename)
        if not os.path.exists(r1_path):
            print(f"  Downloading {r1_filename}...")
            try:
                urllib.request.urlretrieve(sample["fastq_1"], r1_path)
            except Exception as e:
                print(f"  Error downloading R1: {e}")
                continue
        else:
            print(f"  [SKIP] {r1_filename} already exists")

        # Download R2
        r2_filename = os.path.basename(sample["fastq_2"])
        r2_path = os.path.join(test_data_dir, r2_filename)
        if not os.path.exists(r2_path):
            print(f"  Downloading {r2_filename}...")
            try:
                urllib.request.urlretrieve(sample["fastq_2"], r2_path)
            except Exception as e:
                print(f"  Error downloading R2: {e}")
                continue
        else:
            print(f"  [SKIP] {r2_filename} already exists")

        downloaded_samples.append({
            'id': sample_name,
            'fastq_1': r1_path,
            'fastq_2': r2_path,
            'group': sample["group"],
            'single_end': False
        })

    # Generate samplesheet
    samplesheet_path = os.path.join(output_dir, "test_samplesheet.csv")
    with open(samplesheet_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["sample", "fastq_1", "fastq_2", "group"])
        writer.writeheader()
        for sample in downloaded_samples:
            writer.writerow({
                "sample": sample['id'],
                "fastq_1": sample['fastq_1'],
                "fastq_2": sample['fastq_2'],
                "group": sample['group'],
            })

    print(f"\nTest samplesheet written to: {samplesheet_path}")
    print("-" * 60)

    return downloaded_samples, samplesheet_path


def create_site_catalog(execution_site: str, output_dir: str) -> SiteCatalog:
    """Create Pegasus site catalog."""
    sc = SiteCatalog()

    # Local site for staging
    local_site = Site("local")
    local_site.add_directories(
        Directory(Directory.SHARED_SCRATCH, f"{output_dir}/scratch")
            .add_file_servers(FileServer(f"file://{output_dir}/scratch", Operation.ALL)),
        Directory(Directory.LOCAL_STORAGE, f"{output_dir}/storage")
            .add_file_servers(FileServer(f"file://{output_dir}/storage", Operation.ALL))
    )
    sc.add_sites(local_site)

    # Execution site (condorpool)
    exec_site = Site(execution_site)
    exec_site.add_directories(
        Directory(Directory.SHARED_SCRATCH, f"/tmp/{execution_site}")
            .add_file_servers(FileServer(f"file:///tmp/{execution_site}", Operation.ALL))
    )
    exec_site.add_pegasus_profile(style="condor")
    exec_site.add_condor_profile(universe="vanilla")
    exec_site.add_env(LANG="en_US.UTF-8")
    sc.add_sites(exec_site)

    return sc


def create_transformation_catalog(container_image: str) -> Tuple[TransformationCatalog, Container]:
    """Create Pegasus transformation catalog with container."""
    tc = TransformationCatalog()

    # Create container
    container = Container(
        "mag_container",
        Container.SINGULARITY,
        image=container_image
    )
    tc.add_containers(container)

    # Define transformations for each tool
    tools = [
        "fastqc", "fastp", "megahit", "spades", "quast",
        "prodigal", "metabat2", "checkm2", "gtdbtk", "prokka", "multiqc"
    ]

    for tool in tools:
        config = TOOL_CONFIGS.get(tool, {"memory": "4GB", "cores": 2})
        tx = Transformation(
            tool,
            site="local",
            pfn=f"/usr/local/bin/{tool}.sh",
            is_stageable=False,  # Scripts are inside container, don't stage from submit host
            container=container
        )
        tx.add_pegasus_profile(memory=config["memory"], cores=config["cores"])
        tc.add_transformations(tx)

    return tc, container


def create_replica_catalog(samples: List[Dict]) -> ReplicaCatalog:
    """Create replica catalog with input files."""
    rc = ReplicaCatalog()

    for sample in samples:
        # Add forward reads
        if sample['fastq_1'] and os.path.exists(sample['fastq_1']):
            rc.add_replica(
                "local",
                f"{sample['id']}_R1.fastq.gz",
                f"file://{os.path.abspath(sample['fastq_1'])}"
            )

        # Add reverse reads (if paired-end)
        if not sample['single_end'] and sample['fastq_2'] and os.path.exists(sample['fastq_2']):
            rc.add_replica(
                "local",
                f"{sample['id']}_R2.fastq.gz",
                f"file://{os.path.abspath(sample['fastq_2'])}"
            )

    return rc


def create_workflow(
    samples: List[Dict],
    assembler: str = "megahit",
    skip_binning: bool = False,
    skip_taxonomy: bool = False,
    skip_annotation: bool = False,
    skip_fastqc: bool = False,
    gtdbtk_db: Optional[str] = None,
    checkm2_db: Optional[str] = None
) -> Workflow:
    """
    Create the MAG Pegasus workflow.

    Workflow DAG:

    Input FASTQ files
         |
         v
    [FastQC] -----> QC Reports (optional)
         |
         v
    [fastp] -----> Trimmed reads
         |
         v
    [MEGAHIT/SPAdes] -----> Contigs
         |
         v
    [QUAST] -----> Assembly QC
         |
         v
    [Prodigal] -----> Gene predictions
         |
         v
    [MetaBAT2] -----> Genome bins
         |
         v
    [CheckM2] -----> Bin quality
         |
         v
    [GTDB-Tk] -----> Taxonomy
         |
         v
    [Prokka] -----> Annotations
         |
         v
    [MultiQC] -----> Final report
    """
    wf = Workflow("mag-workflow")

    # Track all QC files for MultiQC
    all_qc_files = []
    all_bin_dirs = []

    for sample in samples:
        sample_id = sample['id']
        is_paired = not sample['single_end']

        # Input files
        r1_input = File(f"{sample_id}_R1.fastq.gz")
        r2_input = File(f"{sample_id}_R2.fastq.gz") if is_paired else None

        # ============================================================
        # Step 1: Quality Control - FastQC (raw reads)
        # ============================================================
        if not skip_fastqc:
            fastqc_r1_html = File(f"{sample_id}_R1_fastqc.html")
            fastqc_r1_zip = File(f"{sample_id}_R1_fastqc.zip")

            fastqc_job = Job("fastqc")
            fastqc_job.add_args("--outdir", ".", "--threads", "2")
            fastqc_job.add_inputs(r1_input)
            fastqc_job.add_outputs(fastqc_r1_html, fastqc_r1_zip, stage_out=True)

            if is_paired:
                fastqc_r2_html = File(f"{sample_id}_R2_fastqc.html")
                fastqc_r2_zip = File(f"{sample_id}_R2_fastqc.zip")
                fastqc_job.add_inputs(r2_input)
                fastqc_job.add_outputs(fastqc_r2_html, fastqc_r2_zip, stage_out=True)
                all_qc_files.extend([fastqc_r1_zip, fastqc_r2_zip])
            else:
                all_qc_files.append(fastqc_r1_zip)

            wf.add_jobs(fastqc_job)

        # ============================================================
        # Step 2: Read Trimming - fastp
        # ============================================================
        trimmed_r1 = File(f"{sample_id}_trimmed_R1.fastq.gz")
        fastp_json = File(f"{sample_id}_fastp.json")
        fastp_html = File(f"{sample_id}_fastp.html")

        fastp_job = Job("fastp")
        fastp_job.add_args(
            "-i", r1_input,
            "-o", trimmed_r1,
            "--json", fastp_json,
            "--html", fastp_html,
            "--thread", "4",
            "--qualified_quality_phred", "20",
            "--length_required", "50"
        )
        fastp_job.add_inputs(r1_input)
        fastp_job.add_outputs(trimmed_r1, fastp_json, fastp_html, stage_out=True)

        if is_paired:
            trimmed_r2 = File(f"{sample_id}_trimmed_R2.fastq.gz")
            fastp_job.add_args("-I", r2_input, "-O", trimmed_r2)
            fastp_job.add_inputs(r2_input)
            fastp_job.add_outputs(trimmed_r2, stage_out=True)
        else:
            trimmed_r2 = None

        all_qc_files.append(fastp_json)
        wf.add_jobs(fastp_job)

        # ============================================================
        # Step 3: Assembly - MEGAHIT or SPAdes
        # ============================================================
        contigs = File(f"{sample_id}_contigs.fa")
        assembly_log = File(f"{sample_id}_assembly.log")

        if assembler == "megahit":
            assembly_job = Job("megahit")
            if is_paired:
                assembly_job.add_args(
                    "-1", trimmed_r1,
                    "-2", trimmed_r2,
                    "-o", f"{sample_id}_megahit",
                    "-t", "8",
                    "--min-contig-len", "1000"
                )
                assembly_job.add_inputs(trimmed_r1, trimmed_r2)
            else:
                assembly_job.add_args(
                    "-r", trimmed_r1,
                    "-o", f"{sample_id}_megahit",
                    "-t", "8",
                    "--min-contig-len", "1000"
                )
                assembly_job.add_inputs(trimmed_r1)
        else:  # spades
            assembly_job = Job("spades")
            if is_paired:
                assembly_job.add_args(
                    "-1", trimmed_r1,
                    "-2", trimmed_r2,
                    "-o", f"{sample_id}_spades",
                    "-t", "16",
                    "--meta"
                )
                assembly_job.add_inputs(trimmed_r1, trimmed_r2)
            else:
                assembly_job.add_args(
                    "-s", trimmed_r1,
                    "-o", f"{sample_id}_spades",
                    "-t", "16",
                    "--meta"
                )
                assembly_job.add_inputs(trimmed_r1)

        assembly_job.add_outputs(contigs, assembly_log, stage_out=True)
        assembly_job.add_profiles(Namespace.PEGASUS, key="memory", value="16GB")
        wf.add_jobs(assembly_job)

        # ============================================================
        # Step 4: Assembly QC - QUAST
        # ============================================================
        quast_report = File(f"{sample_id}_quast_report.tsv")
        quast_html = File(f"{sample_id}_quast_report.html")

        quast_job = Job("quast")
        quast_job.add_args(
            contigs,
            "-o", f"{sample_id}_quast",
            "--min-contig", "1000",
            "--threads", "4"
        )
        quast_job.add_inputs(contigs)
        quast_job.add_outputs(quast_report, quast_html, stage_out=True)
        wf.add_jobs(quast_job)

        all_qc_files.append(quast_report)

        # ============================================================
        # Step 5: Gene Prediction - Prodigal
        # ============================================================
        genes_faa = File(f"{sample_id}_genes.faa")
        genes_gff = File(f"{sample_id}_genes.gff")

        prodigal_job = Job("prodigal")
        prodigal_job.add_args(
            "-i", contigs,
            "-a", genes_faa,
            "-o", genes_gff,
            "-f", "gff",
            "-p", "meta"
        )
        prodigal_job.add_inputs(contigs)
        prodigal_job.add_outputs(genes_faa, genes_gff, stage_out=True)
        wf.add_jobs(prodigal_job)

        # ============================================================
        # Step 6: Binning - MetaBAT2
        # ============================================================
        if not skip_binning:
            bins_dir = File(f"{sample_id}_bins")
            depth_file = File(f"{sample_id}_depth.txt")

            # Calculate depth
            depth_job = Job("metabat2")
            depth_job.add_args(
                "jgi_summarize_bam_contig_depths",
                "--outputDepth", depth_file,
                contigs
            )
            depth_job.add_inputs(contigs)
            depth_job.add_outputs(depth_file, stage_out=True)
            wf.add_jobs(depth_job)

            # Run MetaBAT2
            metabat_job = Job("metabat2")
            metabat_job.add_args(
                "-i", contigs,
                "-a", depth_file,
                "-o", f"{sample_id}_bins/bin",
                "-m", "1500",
                "-t", "4"
            )
            metabat_job.add_inputs(contigs, depth_file)
            metabat_job.add_outputs(bins_dir, stage_out=True)
            wf.add_jobs(metabat_job)

            all_bin_dirs.append(bins_dir)

            # ============================================================
            # Step 7: Bin Quality - CheckM2
            # ============================================================
            checkm2_report = File(f"{sample_id}_checkm2_quality.tsv")

            checkm2_job = Job("checkm2")
            checkm2_job.add_args(
                "predict",
                "--input", bins_dir,
                "--output-directory", f"{sample_id}_checkm2",
                "--threads", "8"
            )
            if checkm2_db:
                checkm2_job.add_args("--database_path", checkm2_db)
            checkm2_job.add_inputs(bins_dir)
            checkm2_job.add_outputs(checkm2_report, stage_out=True)
            wf.add_jobs(checkm2_job)

            all_qc_files.append(checkm2_report)

            # ============================================================
            # Step 8: Taxonomy - GTDB-Tk
            # ============================================================
            if not skip_taxonomy:
                gtdbtk_summary = File(f"{sample_id}_gtdbtk.summary.tsv")

                gtdbtk_job = Job("gtdbtk")
                gtdbtk_job.add_args(
                    "classify_wf",
                    "--genome_dir", bins_dir,
                    "--out_dir", f"{sample_id}_gtdbtk",
                    "--extension", "fa",
                    "--cpus", "8"
                )
                if gtdbtk_db:
                    gtdbtk_job.add_args("--gtdbtk_data_path", gtdbtk_db)
                gtdbtk_job.add_inputs(bins_dir, checkm2_report)
                gtdbtk_job.add_outputs(gtdbtk_summary, stage_out=True)
                gtdbtk_job.add_profiles(Namespace.PEGASUS, key="memory", value="64GB")
                wf.add_jobs(gtdbtk_job)

            # ============================================================
            # Step 9: Annotation - Prokka
            # ============================================================
            if not skip_annotation:
                prokka_gff = File(f"{sample_id}_prokka.gff")
                prokka_gbk = File(f"{sample_id}_prokka.gbk")
                prokka_faa = File(f"{sample_id}_prokka.faa")

                prokka_job = Job("prokka")
                prokka_job.add_args(
                    "--outdir", f"{sample_id}_prokka",
                    "--prefix", sample_id,
                    "--metagenome",
                    "--cpus", "4",
                    bins_dir
                )
                prokka_job.add_inputs(bins_dir)
                prokka_job.add_outputs(prokka_gff, prokka_gbk, prokka_faa, stage_out=True)
                wf.add_jobs(prokka_job)

    # ============================================================
    # Step 10: MultiQC Report
    # ============================================================
    multiqc_report = File("multiqc_report.html")
    multiqc_data = File("multiqc_data.json")

    multiqc_job = Job("multiqc")
    multiqc_job.add_args(
        ".",
        "-o", "multiqc_output",
        "--force"
    )
    for qc_file in all_qc_files:
        multiqc_job.add_inputs(qc_file)
    multiqc_job.add_outputs(multiqc_report, multiqc_data, stage_out=True)
    wf.add_jobs(multiqc_job)

    return wf


# Import Namespace for profiles
from Pegasus.api import Namespace


def main():
    parser = argparse.ArgumentParser(
        description="MAG Workflow Generator for Pegasus WMS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with samplesheet
  %(prog)s --samplesheet samples.csv --output workflow.yml

  # Run with nf-core/mag test data (auto-downloads)
  %(prog)s --test --output workflow.yml

  # Quick test (skip resource-intensive steps)
  %(prog)s --test --skip-taxonomy --skip-annotation

  # Use SPAdes assembler with custom databases
  %(prog)s --samplesheet samples.csv --assembler spades \\
      --gtdbtk-db /path/to/gtdbtk_db --checkm2-db /path/to/checkm2_db

  # Skip taxonomy and annotation steps
  %(prog)s --samplesheet samples.csv --skip-taxonomy --skip-annotation

Samplesheet format (CSV):
  sample,fastq_1,fastq_2,group
  sample1,/path/to/sample1_R1.fastq.gz,/path/to/sample1_R2.fastq.gz,group1
  sample2,/path/to/sample2_R1.fastq.gz,/path/to/sample2_R2.fastq.gz,group1
        """
    )

    # Test mode
    parser.add_argument(
        "--test", "-t",
        action="store_true",
        help="Use nf-core/mag test data (auto-downloads ~10MB)"
    )

    # Input/Output arguments
    parser.add_argument(
        "--samplesheet", "-s",
        type=str,
        help="Input samplesheet CSV with sample information (not required with --test)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="workflow.yml",
        help="Output workflow YAML file (default: workflow.yml)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output",
        help="Output directory for results (default: ./output)"
    )

    # Assembly options
    parser.add_argument(
        "--assembler",
        type=str,
        choices=["megahit", "spades"],
        default="megahit",
        help="Assembler to use (default: megahit)"
    )

    # Pipeline control
    parser.add_argument(
        "--skip-binning",
        action="store_true",
        help="Skip genome binning steps"
    )
    parser.add_argument(
        "--skip-taxonomy",
        action="store_true",
        help="Skip GTDB-Tk taxonomy classification"
    )
    parser.add_argument(
        "--skip-annotation",
        action="store_true",
        help="Skip Prokka annotation"
    )
    parser.add_argument(
        "--skip-fastqc",
        action="store_true",
        help="Skip FastQC QC reports"
    )

    # Database paths
    parser.add_argument(
        "--gtdbtk-db",
        type=str,
        help="Path to GTDB-Tk database"
    )
    parser.add_argument(
        "--checkm2-db",
        type=str,
        help="Path to CheckM2 database"
    )

    # Execution options
    parser.add_argument(
        "--execution-site", "-e",
        type=str,
        default="condorpool",
        help="HTCondor execution site name (default: condorpool)"
    )
    parser.add_argument(
        "--container-image",
        type=str,
        default=DEFAULT_CONTAINER,
        help=f"Container image to use (default: {DEFAULT_CONTAINER})"
    )

    args = parser.parse_args()

    # Validate input: either --test or --samplesheet must be provided
    if not args.test and not args.samplesheet:
        print("Error: Either --test or --samplesheet must be provided")
        parser.print_help()
        sys.exit(1)

    # Create output directory first (needed for test data download)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # Handle test mode vs samplesheet mode
    if args.test:
        print("=" * 60)
        print("RUNNING WITH nf-core/mag TEST DATA")
        print("=" * 60)
        samples, samplesheet_path = download_test_data(output_dir)
        if not samples:
            print("Error: Failed to download test data")
            sys.exit(1)
    else:
        # Validate samplesheet exists
        if not os.path.exists(args.samplesheet):
            print(f"Error: Samplesheet not found: {args.samplesheet}")
            sys.exit(1)

        # Parse samplesheet
        print(f"Parsing samplesheet: {args.samplesheet}")
        samples = parse_samplesheet(args.samplesheet)

    if not samples:
        print("Error: No valid samples found")
        sys.exit(1)

    print(f"\nFound {len(samples)} samples:")
    for sample in samples:
        print(f"  - {sample['id']} ({'single-end' if sample['single_end'] else 'paired-end'})")

    # Create catalogs
    print("\nCreating Pegasus catalogs...")
    sc = create_site_catalog(args.execution_site, output_dir)
    tc, container = create_transformation_catalog(args.container_image)
    rc = create_replica_catalog(samples)

    # Create workflow
    print(f"\nCreating MAG workflow with {args.assembler} assembler...")
    wf = create_workflow(
        samples=samples,
        assembler=args.assembler,
        skip_binning=args.skip_binning,
        skip_taxonomy=args.skip_taxonomy,
        skip_annotation=args.skip_annotation,
        skip_fastqc=args.skip_fastqc,
        gtdbtk_db=args.gtdbtk_db,
        checkm2_db=args.checkm2_db
    )

    # Write catalogs
    sc.write("sites.yml")
    tc.write("transformations.yml")
    rc.write("replicas.yml")

    # Write workflow
    wf.add_site_catalog(sc)
    wf.add_transformation_catalog(tc)
    wf.add_replica_catalog(rc)
    wf.write(args.output)

    print(f"\nWorkflow generated successfully!")
    print(f"  Workflow: {args.output}")
    print("  Site catalog: sites.yml")
    print("  Transformation catalog: transformations.yml")
    print("  Replica catalog: replicas.yml")
    print(f"\nTo submit the workflow:")
    print(f"  pegasus-plan --submit -s {args.execution_site} -o local {args.output}")


if __name__ == "__main__":
    main()
