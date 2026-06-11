#!/usr/bin/env python3

"""
Pegasus workflow generator for bacterial RNA-Seq analysis.

Converted from the chienlab-rnaseq Nextflow DSL2 pipeline.

Pipeline steps:
1. fastp        - QC and adapter trimming (per-sample, parallel)
2. bwa_index    - Build BWA index from reference genome
3. bwa_align    - Align reads with BWA-MEM (per-sample, parallel)
4. bam2bigwig   - Generate BigWig files for visualization (per-sample, parallel)
5. count_reads  - Gene-level read quantification with Rsubread featureCounts (fan-in)
6. tmm_normalise - TMM normalization of read counts (edgeR)
7. pca          - Principal component analysis of samples
8. diffexpr     - Differential gene expression with DESeq2 (optional)

Usage:
    ./workflow_generator.py --sample-file samples.tsv --ref-genome ref.fasta \\
                            --ref-ann ref.gff --output workflow.yml

    # With differential expression:
    ./workflow_generator.py --sample-file samples.tsv --ref-genome ref.fasta \\
                            --ref-ann ref.gff --contrast-table contrasts.tsv \\
                            --output workflow.yml
"""

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from Pegasus.api import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Per-tool resource configuration (from Nextflow conf/base.config)
TOOL_CONFIGS = {
    "fastp":          {"memory": "24 GB", "cores": 4},     # process_medium
    "bwa_index":      {"memory": "24 GB", "cores": 4},     # process_medium
    "bwa_align":      {"memory": "46 GB", "cores": 8},     # process_high
    "bam2bigwig":     {"memory": "24 GB", "cores": 4},     # process_medium
    "count_reads":    {"memory": "24 GB", "cores": 4},     # process_medium
    "tmm_normalise":  {"memory": "24 GB", "cores": 4},     # process_medium
    "pca":            {"memory": "24 GB", "cores": 4},     # process_medium
    "diffexpr":       {"memory": "46 GB", "cores": 8},     # process_high
}


class RnaseqWorkflow:
    """Pegasus workflow for bacterial RNA-Seq analysis."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "rnaseq_workflow"

    def __init__(self, args, dagfile="workflow.yml"):
        self.dagfile = dagfile
        self.wf_dir = str(Path(__file__).parent.resolve())
        self.shared_scratch_dir = os.path.join(self.wf_dir, "scratch")
        self.local_storage_dir = os.path.join(self.wf_dir, "output")
        self.args = args
        self.samples = self._parse_sample_file(args.sample_file, args.data_dir)
        self.contrasts = (
            self._parse_contrast_table(args.contrast_table)
            if args.contrast_table
            else []
        )

    def _parse_sample_file(self, sample_file, data_dir):
        """Parse sample TSV file and return list of sample dicts."""
        samples = []
        with open(sample_file, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                if not row.get("sample"):
                    continue
                sample = {
                    "sample": row["sample"].strip(),
                    "file1": row["file1"].strip(),
                    "file2": row.get("file2", "").strip(),
                    "group": row.get("group", "").strip(),
                    "rep_no": row.get("rep_no", "").strip(),
                    "paired": row.get("paired", "0").strip(),
                    "strandedness": row.get("strandedness", "unstranded").strip(),
                }
                # Resolve file paths with data_dir
                if data_dir:
                    sample["file1_path"] = os.path.join(data_dir, sample["file1"])
                    if sample["file2"] and sample["paired"] == "1":
                        sample["file2_path"] = os.path.join(
                            data_dir, sample["file2"]
                        )
                    else:
                        sample["file2_path"] = ""
                else:
                    sample["file1_path"] = sample["file1"]
                    sample["file2_path"] = sample.get("file2", "")

                samples.append(sample)
        return samples

    def _parse_contrast_table(self, contrast_file):
        """Parse contrast table for differential expression."""
        contrasts = []
        with open(contrast_file, newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            next(reader)  # skip header
            for row in reader:
                if len(row) >= 2:
                    contrasts.append(
                        {"group1": row[0].strip(), "group2": row[1].strip()}
                    )
        return contrasts

    def _generate_sample_metadata(self):
        """Generate sample_metadata.tsv for downstream R scripts."""
        metadata_path = os.path.join(self.wf_dir, "sample_metadata.tsv")
        with open(metadata_path, "w") as f:
            f.write(
                "sample\tfile1\tfile2\tgroup\trep_no\tpaired\tstrandedness\n"
            )
            for s in self.samples:
                file1 = s["file1"]
                file2 = s["file2"] if s["paired"] == "1" else ""
                f.write(
                    f"{s['sample']}\t{file1}\t{file2}\t{s['group']}\t"
                    f"{s['rep_no']}\t{s['paired']}\t{s['strandedness']}\n"
                )
        return metadata_path

    def write(self):
        """Write all catalogs and workflow to files."""
        if self.sc is not None:
            self.sc.write()
        self.props.write()
        self.rc.write()
        self.tc.write()
        self.wf.write(file=self.dagfile)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    def create_pegasus_properties(self):
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"

    # ------------------------------------------------------------------
    # Site Catalog
    # ------------------------------------------------------------------
    def create_sites_catalog(self, exec_site_name="condorpool"):
        self.sc = SiteCatalog()

        local = Site("local").add_directories(
            Directory(
                Directory.SHARED_SCRATCH, self.shared_scratch_dir
            ).add_file_servers(
                FileServer(
                    "file://" + self.shared_scratch_dir, Operation.ALL
                )
            ),
            Directory(
                Directory.LOCAL_STORAGE, self.local_storage_dir
            ).add_file_servers(
                FileServer(
                    "file://" + self.local_storage_dir, Operation.ALL
                )
            ),
        )

        exec_site = (
            Site(exec_site_name)
            .add_condor_profile(universe="vanilla")
            .add_pegasus_profile(style="condor")
        )

        self.sc.add_sites(local, exec_site)

    # ------------------------------------------------------------------
    # Transformation Catalog
    # ------------------------------------------------------------------
    def create_transformation_catalog(self, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()

        container = Container(
            "rnaseq_container",
            container_type=Container.SINGULARITY,
            image="docker://kthare10/rnaseq-workflow:latest",
            image_site="docker_hub",
        )

        transformations = []
        for tool_name, config in TOOL_CONFIGS.items():
            tx = Transformation(
                tool_name,
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, f"bin/{tool_name}.py"),
                is_stageable=True,
                container=container,
            ).add_pegasus_profile(
                memory=config["memory"], cores=config.get("cores", 1)
            )
            transformations.append(tx)

        self.tc.add_containers(container)
        self.tc.add_transformations(*transformations)

    # ------------------------------------------------------------------
    # Replica Catalog
    # ------------------------------------------------------------------
    def create_replica_catalog(self):
        self.rc = ReplicaCatalog()

        # Reference genome FASTA
        self.rc.add_replica(
            "local",
            os.path.basename(self.args.ref_genome),
            "file://" + os.path.abspath(self.args.ref_genome),
        )

        # Reference annotation GFF
        self.rc.add_replica(
            "local",
            os.path.basename(self.args.ref_ann),
            "file://" + os.path.abspath(self.args.ref_ann),
        )

        # Sample metadata (generated at workflow creation time)
        metadata_path = self._generate_sample_metadata()
        self.rc.add_replica(
            "local",
            "sample_metadata.tsv",
            "file://" + os.path.abspath(metadata_path),
        )

        # R scripts (support files called by Python wrappers)
        r_scripts = [
            "count_reads.R",
            "TMM_normalise_counts.R",
            "pca.R",
            "diffexpr.R",
        ]
        for script in r_scripts:
            script_path = os.path.join(self.wf_dir, "bin", script)
            if os.path.exists(script_path):
                self.rc.add_replica(
                    "local", script, "file://" + script_path
                )

        # Contrast table (if provided)
        if self.args.contrast_table:
            self.rc.add_replica(
                "local",
                "contrast_table.tsv",
                "file://" + os.path.abspath(self.args.contrast_table),
            )

        # FASTQ files
        for s in self.samples:
            file1_path = os.path.abspath(s["file1_path"])
            if os.path.exists(file1_path):
                self.rc.add_replica(
                    "local",
                    os.path.basename(s["file1"]),
                    "file://" + file1_path,
                )
            else:
                logger.warning(f"FASTQ file not found: {file1_path}")

            if s["paired"] == "1" and s["file2_path"]:
                file2_path = os.path.abspath(s["file2_path"])
                if os.path.exists(file2_path):
                    self.rc.add_replica(
                        "local",
                        os.path.basename(s["file2"]),
                        "file://" + file2_path,
                    )
                else:
                    logger.warning(f"FASTQ file not found: {file2_path}")

    # ------------------------------------------------------------------
    # Workflow DAG
    # ------------------------------------------------------------------
    def create_workflow(self):
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        # Reference file objects
        ref_genome_file = File(os.path.basename(self.args.ref_genome))
        ref_ann_file = File(os.path.basename(self.args.ref_ann))
        metadata_file = File("sample_metadata.tsv")

        # R script file objects
        count_reads_r = File("count_reads.R")
        tmm_normalise_r = File("TMM_normalise_counts.R")
        pca_r = File("pca.R")
        diffexpr_r = File("diffexpr.R")

        # ---- BWA Index (single job) ----
        bwa_idx_files = []
        for ext in ["amb", "ann", "bwt", "pac", "sa"]:
            bwa_idx_files.append(File(f"ref_idx.{ext}"))

        bwa_index_job = (
            Job("bwa_index", _id="bwa_index", node_label="bwa_index")
            .add_args(
                f"--reference {ref_genome_file.lfn}"
            )
            .add_inputs(ref_genome_file)
            .add_outputs(
                *bwa_idx_files, stage_out=False, register_replica=False
            )
        )
        self.wf.add_jobs(bwa_index_job)

        # Track files for fan-in steps
        all_bam_files = []
        all_bai_files = []
        all_count_files = []

        # ---- Per-sample pipeline ----
        for s in self.samples:
            sample_id = s["sample"]
            is_paired = s["paired"] == "1"

            # Input FASTQ files
            read1_file = File(os.path.basename(s["file1"]))
            read2_file = (
                File(os.path.basename(s["file2"])) if is_paired else None
            )

            # -- FASTP (QC + trimming) --
            if is_paired:
                trimmed1 = File(f"{sample_id}_1_trimmed.fq.gz")
                trimmed2 = File(f"{sample_id}_2_trimmed.fq.gz")
            else:
                trimmed1 = File(f"{sample_id}_trimmed.fq.gz")
                trimmed2 = None
            fastp_html = File(f"fastp_qc/{sample_id}_fastp.html")

            fastp_args = (
                f"--read1 {read1_file.lfn} "
                f"--out1 {trimmed1.lfn} "
                f"--html {fastp_html.lfn} "
                f"--threads {TOOL_CONFIGS['fastp']['cores']}"
            )
            fastp_inputs = [read1_file]

            if is_paired:
                fastp_args += (
                    f" --read2 {read2_file.lfn} "
                    f"--out2 {trimmed2.lfn}"
                )
                fastp_inputs.append(read2_file)

            fastp_job = (
                Job(
                    "fastp",
                    _id=f"fastp_{sample_id}",
                    node_label=f"fastp_{sample_id}",
                )
                .add_args(fastp_args)
                .add_inputs(*fastp_inputs)
                .add_outputs(
                    fastp_html, stage_out=True, register_replica=False
                )
                .add_outputs(
                    trimmed1, stage_out=False, register_replica=False
                )
                .add_pegasus_profiles(label=sample_id)
            )
            if is_paired:
                fastp_job.add_outputs(
                    trimmed2, stage_out=False, register_replica=False
                )
            self.wf.add_jobs(fastp_job)

            # -- BWA ALIGN --
            bam_file = File(f"bwa_aln/{sample_id}.bam")
            bai_file = File(f"bwa_aln/{sample_id}.bam.bai")
            counts_file = File(f"{sample_id}.counts")
            all_bam_files.append(bam_file)
            all_bai_files.append(bai_file)
            all_count_files.append(counts_file)

            bwa_args = (
                f"--read1 {trimmed1.lfn} "
                f"--output-bam {bam_file.lfn} "
                f"--output-bai {bai_file.lfn} "
                f"--output-counts {counts_file.lfn} "
                f"--threads {TOOL_CONFIGS['bwa_align']['cores']}"
            )
            bwa_inputs = [trimmed1] + bwa_idx_files
            if is_paired:
                bwa_args += f" --read2 {trimmed2.lfn}"
                bwa_inputs.append(trimmed2)

            bwa_job = (
                Job(
                    "bwa_align",
                    _id=f"bwa_{sample_id}",
                    node_label=f"bwa_{sample_id}",
                )
                .add_args(bwa_args)
                .add_inputs(*bwa_inputs)
                .add_outputs(
                    bam_file, stage_out=True, register_replica=False
                )
                .add_outputs(
                    bai_file, stage_out=True, register_replica=False
                )
                .add_outputs(
                    counts_file, stage_out=False, register_replica=False
                )
                .add_pegasus_profiles(label=sample_id)
            )
            self.wf.add_jobs(bwa_job)

            # -- BAM2BIGWIG (parallel branch from BAM) --
            bw_file = File(f"bigwig/{sample_id}.bw")
            bw_job = (
                Job(
                    "bam2bigwig",
                    _id=f"bw_{sample_id}",
                    node_label=f"bw_{sample_id}",
                )
                .add_args(
                    f"--input-bam {bam_file.lfn} "
                    f"--output {bw_file.lfn} "
                    f"--threads {TOOL_CONFIGS['bam2bigwig']['cores']}"
                )
                .add_inputs(bam_file, bai_file)
                .add_outputs(
                    bw_file, stage_out=True, register_replica=False
                )
                .add_pegasus_profiles(label=sample_id)
            )
            self.wf.add_jobs(bw_job)

        # ---- COUNT_READS (fan-in: all BAMs → gene counts) ----
        # Hierarchical output files (used for both organized output and
        # inter-job data flow; downstream wrappers symlink to flat names)
        gene_counts_out = File("read_counts/gene_counts.tsv")
        gene_counts_pc_out = File("read_counts/gene_counts_pc.tsv")
        ref_gene_df_out = File("read_counts/ref_gene_df.tsv")
        counts_summary_out = File("read_counts/counts_summary.tsv")
        lib_comp_out = File("read_counts/library_composition.png")
        lib_comp_prop_out = File(
            "read_counts/library_composition_proportions.png"
        )

        # Build --bam and --bai args for symlinking in wrapper
        bam_args = " ".join(
            [f"--bam {f.lfn}" for f in all_bam_files]
        )
        bai_args = " ".join(
            [f"--bai {f.lfn}" for f in all_bai_files]
        )

        count_reads_job = (
            Job(
                "count_reads",
                _id="count_reads",
                node_label="count_reads",
            )
            .add_args(
                f"--metadata sample_metadata.tsv "
                f"--gff {ref_ann_file.lfn} "
                f"--threads {TOOL_CONFIGS['count_reads']['cores']} "
                + bam_args + " " + bai_args
            )
            .add_inputs(
                metadata_file,
                ref_ann_file,
                count_reads_r,
                *all_bam_files,
                *all_bai_files,
                *all_count_files,
            )
            # Hierarchical outputs (also used as inputs by downstream jobs)
            .add_outputs(
                gene_counts_out, gene_counts_pc_out,
                ref_gene_df_out, counts_summary_out,
                lib_comp_out, lib_comp_prop_out,
                stage_out=True, register_replica=False,
            )
        )
        self.wf.add_jobs(count_reads_job)

        # ---- TMM_NORMALISE ----
        cpm_counts_out = File("read_counts/cpm_counts.tsv")
        rpkm_counts_out = File("read_counts/rpkm_counts.tsv")

        tmm_job = (
            Job(
                "tmm_normalise",
                _id="tmm_normalise",
                node_label="tmm_normalise",
            )
            .add_args("--log-transform TRUE")
            .add_inputs(gene_counts_out, ref_gene_df_out, tmm_normalise_r)
            .add_outputs(
                cpm_counts_out, rpkm_counts_out,
                stage_out=True, register_replica=False,
            )
        )
        self.wf.add_jobs(tmm_job)

        # ---- PCA ----
        pca_rds = File("PCA_samples/pca.rds")
        pca_coords = File("PCA_samples/pca_coords.tsv")
        pca_plot = File("PCA_samples/pca_grouped.png")

        pca_job = (
            Job("pca", _id="pca_samples", node_label="pca_samples")
            .add_inputs(cpm_counts_out, metadata_file, pca_r)
            .add_outputs(
                pca_rds,
                pca_coords,
                pca_plot,
                stage_out=True,
                register_replica=False,
            )
        )
        self.wf.add_jobs(pca_job)

        # ---- DIFF_EXPRESSION (optional) ----
        if self.args.contrast_table and self.contrasts:
            contrast_file = File("contrast_table.tsv")

            diffexpr_outputs = []
            for c in self.contrasts:
                name = f"{c['group1']}_{c['group2']}"
                diffexpr_outputs.append(
                    File(f"diff_expr/DGE_{name}.tsv")
                )
                diffexpr_outputs.append(
                    File(f"diff_expr/volcano_plot_{name}.png")
                )

            diffexpr_job = (
                Job("diffexpr", _id="diffexpr", node_label="diffexpr")
                .add_args(
                    f"--p-threshold {self.args.p_thresh} "
                    f"--l2fc-threshold {self.args.l2fc_thresh}"
                )
                .add_inputs(
                    gene_counts_out,
                    metadata_file,
                    contrast_file,
                    diffexpr_r,
                )
            )
            for out_f in diffexpr_outputs:
                diffexpr_job.add_outputs(
                    out_f, stage_out=True, register_replica=False
                )
            self.wf.add_jobs(diffexpr_job)

        logger.info(
            f"Workflow created: {len(self.samples)} sample(s), "
            f"{len(self.contrasts)} contrast(s)"
        )


# ======================================================================
# main()
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Pegasus RNA-Seq Workflow Generator "
        "(converted from chienlab-rnaseq Nextflow pipeline)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --sample-file samples.tsv --ref-genome ref.fasta --ref-ann ref.gff
  %(prog)s --sample-file samples.tsv --ref-genome ref.fasta --ref-ann ref.gff \\
           --contrast-table contrasts.tsv --data-dir /path/to/fastq/
""",
    )

    # Standard Pegasus arguments
    parser.add_argument(
        "-s",
        "--skip-sites-catalog",
        action="store_true",
        help="Skip site catalog creation",
    )
    parser.add_argument(
        "-e",
        "--execution-site-name",
        metavar="STR",
        type=str,
        default="condorpool",
        help="Execution site name (default: condorpool)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="STR",
        type=str,
        default="workflow.yml",
        help="Output file (default: workflow.yml)",
    )

    # Workflow-specific arguments
    parser.add_argument(
        "--sample-file",
        type=str,
        required=True,
        help="Sample metadata TSV file "
        "(columns: sample, file1, file2, group, rep_no, paired, strandedness)",
    )
    parser.add_argument(
        "--ref-genome",
        type=str,
        required=True,
        help="Reference genome FASTA file",
    )
    parser.add_argument(
        "--ref-ann",
        type=str,
        required=True,
        help="Reference annotation GFF file",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Directory containing FASTQ files "
        "(prepended to file1/file2 in sample sheet)",
    )
    parser.add_argument(
        "--contrast-table",
        type=str,
        default=None,
        help="Contrast table TSV for differential expression (optional)",
    )
    parser.add_argument(
        "--p-thresh",
        type=float,
        default=0.05,
        help="Adjusted p-value threshold for DE (default: 0.05)",
    )
    parser.add_argument(
        "--l2fc-thresh",
        type=float,
        default=1.0,
        help="Log2 fold change threshold for DE (default: 1.0)",
    )

    args = parser.parse_args()

    # Validation
    if not os.path.exists(args.sample_file):
        logger.error(f"Sample file not found: {args.sample_file}")
        sys.exit(1)
    if not os.path.exists(args.ref_genome):
        logger.error(f"Reference genome not found: {args.ref_genome}")
        sys.exit(1)
    if not os.path.exists(args.ref_ann):
        logger.error(f"Reference annotation not found: {args.ref_ann}")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("RNA-SEQ WORKFLOW GENERATOR")
    logger.info("=" * 70)
    logger.info(f"Sample file:    {args.sample_file}")
    logger.info(f"Reference:      {args.ref_genome}")
    logger.info(f"Annotation:     {args.ref_ann}")
    logger.info(f"Data directory: {args.data_dir or '(none)'}")
    logger.info(f"Contrast table: {args.contrast_table or '(none)'}")
    logger.info(f"Execution site: {args.execution_site_name}")
    logger.info(f"Output file:    {args.output}")
    logger.info("=" * 70)

    try:
        workflow = RnaseqWorkflow(args, dagfile=args.output)

        logger.info(f"Parsed {len(workflow.samples)} sample(s)")
        for s in workflow.samples:
            pe = "PE" if s["paired"] == "1" else "SE"
            logger.info(
                f"  {s['sample']} ({pe}, {s['strandedness']}, "
                f"group={s['group']})"
            )
        if workflow.contrasts:
            logger.info(f"Contrasts: {len(workflow.contrasts)}")
            for c in workflow.contrasts:
                logger.info(f"  {c['group1']} vs {c['group2']}")

        workflow.create_pegasus_properties()

        if not args.skip_sites_catalog:
            workflow.create_sites_catalog(
                exec_site_name=args.execution_site_name
            )

        workflow.create_transformation_catalog(
            exec_site_name=args.execution_site_name
        )
        workflow.create_replica_catalog()
        workflow.create_workflow()
        workflow.write()

        logger.info(f"\nWorkflow written to {args.output}")
        logger.info(
            f"Submit: pegasus-plan --submit "
            f"-s {args.execution_site_name} -o local {args.output}"
        )

    except Exception as e:
        logger.error(f"Failed to generate workflow: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
