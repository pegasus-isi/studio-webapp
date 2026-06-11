#!/usr/bin/env python3

"""
Pegasus workflow generator for bacterial TNseq alignment and annotation.

This converts the chienlab-tnseq Snakemake workflow to Pegasus WMS.

Pipeline steps:
1. clip - Clip UMIs using Je
2. seqkit_grep - Filter reads by transposon sequence
3. bwa_mem - Align reads with BWA-MEM
4. rm_dupe - Remove PCR duplicates using Je
5. genomecov - Assign genome positions using bedtools
6. mapping - Map counts to gene features (4 variants: total/unique x mid/full)
7. bam2bw - Generate BigWig files
8. tab - Create tab files from mapped data
9. concat - Concatenate all sample tab files into final TSVs
10. qc - Quality control statistics

Usage:
    ./workflow_generator.py --samples sample1 sample2 --ref-fasta ref.fasta \\
                            --ref-mid ref_mid.bed --ref-full ref_full.bed \\
                            --output workflow.yml
"""

import os
import sys
import glob
from pathlib import Path
from argparse import ArgumentParser

from Pegasus.api import *


class TNseqWorkflow:
    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "tnseq_workflow"

    def __init__(
        self,
        samples,
        fastq_dir,
        ref_fasta,
        ref_mid,
        ref_full,
        transposon_seq="TGTATAAGAG",
        dagfile="workflow.yml"
    ):
        self.dagfile = dagfile
        self.wf_dir = str(Path(__file__).parent.resolve())
        self.shared_scratch_dir = os.path.join(self.wf_dir, "scratch")
        self.local_storage_dir = os.path.join(self.wf_dir, "output")
        self.samples = samples
        self.fastq_dir = fastq_dir
        self.ref_fasta = ref_fasta
        self.ref_mid = ref_mid
        self.ref_full = ref_full
        self.transposon_seq = transposon_seq

    def write(self):
        if self.sc is not None:
            self.sc.write()
        self.props.write()
        self.rc.write()
        self.tc.write()
        self.wf.write(file=self.dagfile)

    def create_pegasus_properties(self):
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"
        return

    def create_sites_catalog(self, exec_site_name="condorpool"):
        self.sc = SiteCatalog()

        local = Site("local").add_directories(
            Directory(
                Directory.SHARED_SCRATCH, self.shared_scratch_dir
            ).add_file_servers(
                FileServer("file://" + self.shared_scratch_dir, Operation.ALL)
            ),
            Directory(Directory.LOCAL_STORAGE, self.local_storage_dir).add_file_servers(
                FileServer("file://" + self.local_storage_dir, Operation.ALL)
            ),
        )

        exec_site = (
            Site(exec_site_name)
            .add_condor_profile(universe="vanilla")
            .add_pegasus_profile(style="condor")
        )

        self.sc.add_sites(local, exec_site)

    def create_transformation_catalog(self, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()

        # Container with all bioinformatics tools
        tnseq_container = Container(
            "tnseq_container",
            container_type=Container.SINGULARITY,
            image="docker://kthare10/tnseq:latest",
            image_site="docker_hub",
        )

        # Transformations
        mkdir = Transformation(
            "mkdir", site="local", pfn="/bin/mkdir", is_stageable=False
        )

        clip = Transformation(
            "clip",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/clip.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="6 GB")

        seqkit_grep = Transformation(
            "seqkit_grep",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/seqkit_grep.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB", cores=4)

        bwa_mem = Transformation(
            "bwa_mem",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/bwa_mem.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="6 GB", cores=4)

        rm_dupe = Transformation(
            "rm_dupe",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/rm_dupe.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="6 GB")

        genomecov = Transformation(
            "genomecov",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/genomecov.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB")

        bedtools_map = Transformation(
            "bedtools_map",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/bedtools_map.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB")

        bam2bw = Transformation(
            "bam2bw",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/bam2bw.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="4 GB", cores=2)

        tab_generate = Transformation(
            "tab_generate",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/tab_generate.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB")

        concat = Transformation(
            "concat",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/concat.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB")

        seqkit_qc = Transformation(
            "seqkit_qc",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/seqkit_qc.py"),
            is_stageable=True,
            container=tnseq_container,
        ).add_pegasus_profile(memory="2 GB", cores=4)

        self.tc.add_containers(tnseq_container)
        self.tc.add_transformations(
            mkdir, clip, seqkit_grep, bwa_mem, rm_dupe, genomecov,
            bedtools_map, bam2bw, tab_generate, concat, seqkit_qc
        )

    def create_replica_catalog(self):
        self.rc = ReplicaCatalog()

        # Add reference files
        self.rc.add_replica(
            "local",
            os.path.basename(self.ref_fasta),
            "file://" + os.path.abspath(self.ref_fasta)
        )
        # BWA index files
        for ext in ['.amb', '.ann', '.bwt', '.pac', '.sa']:
            idx_file = self.ref_fasta + ext
            if os.path.exists(idx_file):
                self.rc.add_replica(
                    "local",
                    os.path.basename(idx_file),
                    "file://" + os.path.abspath(idx_file)
                )

        self.rc.add_replica(
            "local",
            os.path.basename(self.ref_mid),
            "file://" + os.path.abspath(self.ref_mid)
        )
        self.rc.add_replica(
            "local",
            os.path.basename(self.ref_full),
            "file://" + os.path.abspath(self.ref_full)
        )

        # Add Je JAR file
        je_jar = os.path.join(self.wf_dir, "bin/je_1.2_bundle.jar")
        if os.path.exists(je_jar):
            self.rc.add_replica("local", "je_1.2_bundle.jar", "file://" + je_jar)

        # Add R scripts
        tab_r = os.path.join(self.wf_dir, "bin/tab.R")
        if os.path.exists(tab_r):
            self.rc.add_replica("local", "tab.R", "file://" + tab_r)
        concat_r = os.path.join(self.wf_dir, "bin/concat.R")
        if os.path.exists(concat_r):
            self.rc.add_replica("local", "concat.R", "file://" + concat_r)

        # Add sample FASTQ files
        for sample in self.samples:
            fastq_path = os.path.join(self.fastq_dir, f"{sample}.fq.gz")
            if os.path.exists(fastq_path):
                self.rc.add_replica(
                    "local",
                    f"{sample}.fq.gz",
                    "file://" + os.path.abspath(fastq_path)
                )
            else:
                print(f"Warning: FASTQ file not found: {fastq_path}")

    def create_workflow(self):
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        # Reference files
        ref_fasta_file = File(os.path.basename(self.ref_fasta))
        ref_mid_file = File(os.path.basename(self.ref_mid))
        ref_full_file = File(os.path.basename(self.ref_full))
        je_jar = File("je_1.2_bundle.jar")
        tab_r_file = File("tab.R")
        concat_r_file = File("concat.R")

        # BWA index files
        bwa_idx_files = []
        for ext in ['.amb', '.ann', '.bwt', '.pac', '.sa']:
            bwa_idx_files.append(File(os.path.basename(self.ref_fasta) + ext))

        print(f"\nCreating workflow for {len(self.samples)} sample(s)")

        # Create output directories
        mkdir_job = (
            Job("mkdir", _id="mkdir_dirs", node_label="mkdir_dirs")
            .add_args(
                f"-p {self.local_storage_dir}/preprocess "
                f"{self.local_storage_dir}/bwa_aln "
                f"{self.local_storage_dir}/bwa_aln/dedup_metrics "
                f"{self.local_storage_dir}/bigwig "
                f"{self.local_storage_dir}/mapped "
                f"{self.local_storage_dir}/read_counts/totalcounts_mid "
                f"{self.local_storage_dir}/read_counts/totalcounts_full "
                f"{self.local_storage_dir}/read_counts/uniquecounts_mid "
                f"{self.local_storage_dir}/read_counts/uniquecounts_full "
                f"{self.local_storage_dir}/QC"
            )
            .add_profiles(Namespace.SELECTOR, key="execution.site", value="local")
        )
        self.wf.add_jobs(mkdir_job)

        # Track files for QC and concat jobs
        clipped_files = []
        pruned_files = []
        bam_files = []
        marked_bam_files = []
        tab_files = {
            'totalcounts_mid': [],
            'totalcounts_full': [],
            'uniquecounts_mid': [],
            'uniquecounts_full': []
        }

        # Process each sample
        for sample in self.samples:
            print(f"  Adding jobs for sample: {sample}")

            fastq_file = File(f"{sample}.fq.gz")

            # Step 1: Clip UMIs
            clipped_file = File(f"preprocess/{sample}_clipped.fq.gz")
            clipped_files.append(clipped_file)
            clip_job = (
                Job("clip", _id=f"clip_{sample}", node_label=f"clip_{sample}")
                .add_args(
                    f"--input {sample}.fq.gz "
                    f"--output preprocess/{sample}_clipped.fq.gz "
                    f"--je-jar je_1.2_bundle.jar "
                    f"--umi-len 6"
                )
                .add_inputs(fastq_file, je_jar)
                .add_outputs(clipped_file, stage_out=False, register_replica=False)
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(clip_job)
            self.wf.add_dependency(mkdir_job, children=[clip_job])

            # Step 2: Seqkit grep (filter by transposon sequence)
            pruned_file = File(f"preprocess/{sample}_pruned.fq.gz")
            pruned_files.append(pruned_file)
            seqkit_job = (
                Job("seqkit_grep", _id=f"seqkit_{sample}", node_label=f"seqkit_{sample}")
                .add_args(
                    f"--input preprocess/{sample}_clipped.fq.gz "
                    f"--output preprocess/{sample}_pruned.fq.gz "
                    f"--pattern {self.transposon_seq} "
                    f"--threads 4"
                )
                .add_inputs(clipped_file)
                .add_outputs(pruned_file, stage_out=False, register_replica=False)
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(seqkit_job)

            # Step 3: BWA MEM alignment
            bam_file = File(f"bwa_aln/{sample}.bam")
            bam_files.append(bam_file)
            bwa_job = (
                Job("bwa_mem", _id=f"bwa_{sample}", node_label=f"bwa_{sample}")
                .add_args(
                    f"--input preprocess/{sample}_pruned.fq.gz "
                    f"--output bwa_aln/{sample}.bam "
                    f"--reference {os.path.basename(self.ref_fasta)} "
                    f"--threads 4"
                )
                .add_inputs(pruned_file, ref_fasta_file, *bwa_idx_files)
                .add_outputs(bam_file, stage_out=False, register_replica=False)
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(bwa_job)

            # Step 4: Remove PCR duplicates
            marked_bam = File(f"bwa_aln/{sample}_marked.bam")
            dedup_metrics = File(f"bwa_aln/dedup_metrics/{sample}_metrics.txt")
            marked_bam_files.append(marked_bam)
            dupe_job = (
                Job("rm_dupe", _id=f"dupe_{sample}", node_label=f"dupe_{sample}")
                .add_args(
                    f"--input bwa_aln/{sample}.bam "
                    f"--output bwa_aln/{sample}_marked.bam "
                    f"--metrics bwa_aln/dedup_metrics/{sample}_metrics.txt "
                    f"--je-jar je_1.2_bundle.jar"
                )
                .add_inputs(bam_file, je_jar)
                .add_outputs(
                    marked_bam, dedup_metrics,
                    stage_out=True, register_replica=False
                )
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(dupe_job)

            # Step 5: Generate BigWig (parallel branch)
            bw_file = File(f"bigwig/{sample}.bw")
            bai_file = File(f"bwa_aln/{sample}_marked.bam.bai")
            bw_job = (
                Job("bam2bw", _id=f"bw_{sample}", node_label=f"bw_{sample}")
                .add_args(
                    f"--input bwa_aln/{sample}_marked.bam "
                    f"--output bigwig/{sample}.bw "
                    f"--threads 2"
                )
                .add_inputs(marked_bam)
                .add_outputs(
                    bw_file, bai_file,
                    stage_out=True, register_replica=False
                )
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(bw_job)

            # Step 6: Genomecov
            count_file = File(f"mapped/{sample}_count.txt")
            genomecov_job = (
                Job("genomecov", _id=f"gcov_{sample}", node_label=f"gcov_{sample}")
                .add_args(
                    f"--input bwa_aln/{sample}_marked.bam "
                    f"--output mapped/{sample}_count.txt"
                )
                .add_inputs(marked_bam)
                .add_outputs(count_file, stage_out=False, register_replica=False)
                .add_pegasus_profiles(label=sample)
            )
            self.wf.add_jobs(genomecov_job)

            # Step 7: Mapping (4 variants)
            mapping_types = [
                ('totalcounts_mid', ref_mid_file, 'sum', 'middle_totalsum'),
                ('totalcounts_full', ref_full_file, 'sum', 'full_totalsum'),
                ('uniquecounts_mid', ref_mid_file, 'count', 'middle_uniquesum'),
                ('uniquecounts_full', ref_full_file, 'count', 'full_uniquesum'),
            ]

            for map_type, ref_bed, operation, suffix in mapping_types:
                mapped_file = File(f"mapped/{sample}_{suffix}.txt")
                map_job = (
                    Job("bedtools_map", _id=f"map_{map_type}_{sample}", node_label=f"map_{map_type}_{sample}")
                    .add_args(
                        f"--input mapped/{sample}_count.txt "
                        f"--reference {ref_bed.lfn} "
                        f"--output mapped/{sample}_{suffix}.txt "
                        f"--operation {operation}"
                    )
                    .add_inputs(count_file, ref_bed)
                    .add_outputs(mapped_file, stage_out=False, register_replica=False)
                    .add_pegasus_profiles(label=sample)
                )
                self.wf.add_jobs(map_job)

                # Step 8: Generate tab files
                tab_suffix = 'totalcounts' if 'total' in map_type else 'uniquecounts'
                tab_filename = f"{sample}.{tab_suffix}.tab"
                tab_file = File(f"read_counts/{map_type}/{tab_filename}")
                tab_files[map_type].append(tab_file)
                tab_job = (
                    Job("tab_generate", _id=f"tab_{map_type}_{sample}", node_label=f"tab_{map_type}_{sample}")
                    .add_args(
                        f"--input mapped/{sample}_{suffix}.txt "
                        f"--output read_counts/{map_type}/{tab_filename}"
                    )
                    .add_inputs(mapped_file, tab_r_file)
                    .add_outputs(tab_file, stage_out=True, register_replica=False)
                    .add_pegasus_profiles(label=sample)
                )
                self.wf.add_jobs(tab_job)

        # Step 9: Concat jobs (merge all sample tab files)
        concat_configs = [
            ('totalcounts_mid', ref_mid_file, 'totalcounts_mid.tsv'),
            ('totalcounts_full', ref_full_file, 'totalcounts_full.tsv'),
            ('uniquecounts_mid', ref_mid_file, 'uniquecounts_mid.tsv'),
            ('uniquecounts_full', ref_full_file, 'uniquecounts_full.tsv'),
        ]

        for map_type, ref_bed, output_name in concat_configs:
            output_tsv = File(f"read_counts/{output_name}")
            tab_file_args = ' '.join([f"--input {f.lfn}" for f in tab_files[map_type]])
            concat_job = (
                Job("concat", _id=f"concat_{map_type}", node_label=f"concat_{map_type}")
                .add_args(
                    f"--reference {ref_bed.lfn} "
                    f"--output read_counts/{output_name} "
                    + tab_file_args
                )
                .add_inputs(ref_bed, concat_r_file, *tab_files[map_type])
                .add_outputs(output_tsv, stage_out=True, register_replica=False)
            )
            self.wf.add_jobs(concat_job)

        # Step 10: QC jobs
        # FASTQ QC
        qc_fq_file = File("QC/QC_fq.txt")
        fq_qc_job = (
            Job("seqkit_qc", _id="qc_fastq", node_label="qc_fastq")
            .add_args(
                f"--mode fastq "
                f"--output QC/QC_fq.txt "
                f"--threads 4 "
                + ' '.join([f"--input {f.lfn}" for f in clipped_files + pruned_files])
            )
            .add_inputs(*clipped_files, *pruned_files)
            .add_outputs(qc_fq_file, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(fq_qc_job)

        # BAM QC
        qc_bam_file = File("QC/QC_bam.txt")
        bam_qc_job = (
            Job("seqkit_qc", _id="qc_bam", node_label="qc_bam")
            .add_args(
                f"--mode bam "
                f"--output QC/QC_bam.txt "
                f"--threads 4 "
                + ' '.join([f"--input {f.lfn}" for f in bam_files + marked_bam_files])
            )
            .add_inputs(*bam_files, *marked_bam_files)
            .add_outputs(qc_bam_file, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(bam_qc_job)

        print("\nWorkflow created successfully!")
        print(f"  Total samples: {len(self.samples)}")
        print(f"  Pipeline: clip -> seqkit_grep -> bwa_mem -> rm_dupe -> genomecov -> mapping -> tab -> concat")


def discover_samples(fastq_dir):
    """Discover sample names from FASTQ directory."""
    samples = []
    for fq in glob.glob(os.path.join(fastq_dir, "*.fq.gz")):
        sample = os.path.basename(fq).replace(".fq.gz", "")
        samples.append(sample)
    return sorted(samples)


if __name__ == "__main__":
    parser = ArgumentParser(description="Pegasus TNseq Workflow Generator")

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
    parser.add_argument(
        "--samples",
        metavar="STR",
        type=str,
        nargs="+",
        default=None,
        help="Sample names (without .fq.gz extension). If not provided, auto-discovers from fastq-dir.",
    )
    parser.add_argument(
        "--fastq-dir",
        metavar="STR",
        type=str,
        required=True,
        help="Directory containing input FASTQ files (*.fq.gz)",
    )
    parser.add_argument(
        "--ref-fasta",
        metavar="STR",
        type=str,
        required=True,
        help="Reference genome FASTA file",
    )
    parser.add_argument(
        "--ref-mid",
        metavar="STR",
        type=str,
        required=True,
        help="Reference BED file for mid (clipped) gene features",
    )
    parser.add_argument(
        "--ref-full",
        metavar="STR",
        type=str,
        required=True,
        help="Reference BED file for full gene features",
    )
    parser.add_argument(
        "--transposon-seq",
        metavar="STR",
        type=str,
        default="TGTATAAGAG",
        help="Transposon static region sequence (default: TGTATAAGAG)",
    )

    args = parser.parse_args()

    # Auto-discover samples if not provided
    if args.samples is None:
        args.samples = discover_samples(args.fastq_dir)
        if not args.samples:
            print(f"Error: No FASTQ files found in {args.fastq_dir}")
            sys.exit(1)
        print(f"Auto-discovered {len(args.samples)} samples: {', '.join(args.samples)}")

    print("=" * 70)
    print("TNSEQ WORKFLOW GENERATOR")
    print("=" * 70)
    print(f"Samples: {len(args.samples)}")
    print(f"FASTQ directory: {args.fastq_dir}")
    print(f"Reference FASTA: {args.ref_fasta}")
    print(f"Reference (mid): {args.ref_mid}")
    print(f"Reference (full): {args.ref_full}")
    print(f"Transposon sequence: {args.transposon_seq}")
    print(f"Execution site: {args.execution_site_name}")
    print("=" * 70)

    try:
        workflow = TNseqWorkflow(
            samples=args.samples,
            fastq_dir=args.fastq_dir,
            ref_fasta=args.ref_fasta,
            ref_mid=args.ref_mid,
            ref_full=args.ref_full,
            transposon_seq=args.transposon_seq,
            dagfile=args.output
        )

        print("\nGenerating workflow...")
        workflow.create_pegasus_properties()

        if not args.skip_sites_catalog:
            workflow.create_sites_catalog(exec_site_name=args.execution_site_name)

        workflow.create_transformation_catalog(exec_site_name=args.execution_site_name)
        workflow.create_replica_catalog()
        workflow.create_workflow()
        workflow.write()

        print(f"\nWorkflow written to {args.output}")
        print(f"\nTo submit the workflow:")
        print(f"  pegasus-plan --submit -s {args.execution_site_name} -o local {args.output}")

    except Exception as e:
        print(f"\nError creating workflow: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
