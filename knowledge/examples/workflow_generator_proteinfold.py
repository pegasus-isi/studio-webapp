#!/usr/bin/env python3

"""
Pegasus workflow generator for chienlab-proteinfold.

Converted from the Nextflow pipeline at:
  https://github.com/baldikacti/chienlab-proteinfold

Supports three protein structure prediction modes:
  - colabfold:  ColabFold batch predictions
  - alphafold3: AlphaFold3 MSA + fold inference
  - boltz:      Boltz structure prediction

Pipeline DAGs:
  ColabFold:  process_tsv -> [per-pair: colabfold_batch] -> rank_af
  AlphaFold3: process_tsv -> [per-pair: af3_msa] -> [per-batch: af3_fold] -> rank_af
  Boltz:      process_tsv -> [per-batch: boltz_predict] -> rank_af

Usage:
  # ColabFold
  ./workflow_generator.py --mode colabfold --input acclist.tsv \
      --colabfold-cache-dir /path/to/cache --output workflow.yml

  # AlphaFold3
  ./workflow_generator.py --mode alphafold3 --input acclist.tsv \
      --db-dir /path/to/af3db --model-dir /path/to/models --output workflow.yml

  # Boltz
  ./workflow_generator.py --mode boltz --input acclist.tsv \
      --model boltz2 --boltz-cache-dir /path/to/cache --output workflow.yml
"""

import argparse
import csv
import logging
import os
import re
import sys
from itertools import islice
from pathlib import Path

from Pegasus.api import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-tool resource configuration
# ---------------------------------------------------------------------------
TOOL_CONFIGS = {
    "process_tsv":       {"memory": "6 GB",   "cores": 1},
    "colabfold_batch":   {"memory": "30 GB",  "cores": 1},
    "af3_msa":           {"memory": "100 GB", "cores": 10},
    "af3_fold":          {"memory": "30 GB",  "cores": 1},
    "boltz_predict":     {"memory": "30 GB",  "cores": 1},
    "rank_af":           {"memory": "6 GB",   "cores": 1},
}


# ---------------------------------------------------------------------------
# Utility: batch an iterable into chunks of size n
# ---------------------------------------------------------------------------
def _batched(iterable, n):
    """Batch data into lists of length n. The last batch may be shorter."""
    it = iter(iterable)
    while True:
        batch = list(islice(it, n))
        if not batch:
            break
        yield batch


# ---------------------------------------------------------------------------
# Utility: predict output filenames from TSV input
# ---------------------------------------------------------------------------
def _read_fasta_headers(fasta_path):
    """Read FASTA sequence headers (without '>' prefix)."""
    headers = []
    with open(fasta_path) as f:
        for line in f:
            if line.startswith(">"):
                header = line[1:].strip().split()[0]
                headers.append(header)
    return headers


def _get_entry_names(entry, workdir="."):
    """Get display name(s) for a TSV entry.

    For FASTA files: returns a list of sequence headers.
    For everything else: returns [entry].
    """
    if entry.endswith(".fasta") or entry.endswith(".fa"):
        path = os.path.join(workdir, entry) if not os.path.isabs(entry) else entry
        if os.path.exists(path):
            headers = _read_fasta_headers(path)
            return headers if headers else [Path(entry).stem]
        else:
            logger.warning(f"FASTA file not found for name prediction: {path}")
            return [Path(entry).stem]
    else:
        return [entry]


def _safe_name(name):
    """Sanitize a name for filesystem use (matches tsv2json.py logic)."""
    return re.sub(r"[^\w\-_.]", "_", name)


def compute_output_filenames(tsv_path, mode, workdir="."):
    """Compute expected output filenames from tsv2json.py.

    This replicates the filename logic from tsv2json.py so we can declare
    Pegasus File objects for dependency inference at generation time.
    """
    ext = "json" if mode == "alphafold3" else "fasta"

    entries = []
    with open(tsv_path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        reader.fieldnames = [h.lower().strip('"') for h in reader.fieldnames]
        for row in reader:
            entry = row["entry"].strip('"')
            bait = int(row["bait"].strip('"'))
            entries.append((entry, bait))

    baits = [e for e, b in entries if b == 1]
    preys = [e for e, b in entries if b == 0]

    filenames = []
    for bait in baits:
        bait_names = _get_entry_names(bait, workdir)
        for prey in preys:
            prey_names = _get_entry_names(prey, workdir)
            for bn in bait_names:
                for pn in prey_names:
                    filenames.append(
                        f"{_safe_name(bn)}_{_safe_name(pn)}.{ext}"
                    )

    return filenames


def find_fasta_references(tsv_path):
    """Find FASTA file paths referenced in the TSV."""
    fasta_files = []
    with open(tsv_path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        reader.fieldnames = [h.lower().strip('"') for h in reader.fieldnames]
        for row in reader:
            entry = row["entry"].strip('"')
            if entry.endswith(".fasta") or entry.endswith(".fa"):
                fasta_files.append(entry)
    return fasta_files


# ---------------------------------------------------------------------------
# Workflow class
# ---------------------------------------------------------------------------
class ProteinfoldWorkflow:
    """Pegasus workflow for protein structure prediction."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "proteinfold_workflow"

    def __init__(self, dagfile="workflow.yml"):
        self.dagfile = dagfile
        self.wf_dir = str(Path(__file__).parent.resolve())
        self.shared_scratch_dir = os.path.join(self.wf_dir, "scratch")
        self.local_storage_dir = os.path.join(self.wf_dir, "output")

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
                FileServer("file://" + self.shared_scratch_dir, Operation.ALL)
            ),
            Directory(
                Directory.LOCAL_STORAGE, self.local_storage_dir
            ).add_file_servers(
                FileServer("file://" + self.local_storage_dir, Operation.ALL)
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
    def create_transformation_catalog(self, args, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()

        # --- Containers ---
        py_container = Container(
            "proteinfold_py",
            container_type=Container.SINGULARITY,
            image="docker://kthare10/chienlab_proteinfold_py:pegasus",
            image_site="docker_hub",
        )

        containers = [py_container]
        transformations = []

        # process_tsv — reuses tsv2json.py directly
        process_tsv = Transformation(
            "process_tsv",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/tsv2json.py"),
            is_stageable=True,
            container=py_container,
        ).add_pegasus_profile(
            memory=TOOL_CONFIGS["process_tsv"]["memory"],
            cores=TOOL_CONFIGS["process_tsv"]["cores"],
        )
        transformations.append(process_tsv)

        # rank_af — reuses rank_af.py directly
        rank_af = Transformation(
            "rank_af",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/rank_af.py"),
            is_stageable=True,
            container=py_container,
        ).add_pegasus_profile(
            memory=TOOL_CONFIGS["rank_af"]["memory"],
            cores=TOOL_CONFIGS["rank_af"]["cores"],
        )
        transformations.append(rank_af)

        # --- Mode-specific transformations ---
        if args.mode == "colabfold":
            cf_container = Container(
                "colabfold",
                container_type=Container.SINGULARITY,
                image="docker://kthare10/colabfold:pegasus",
                image_site="docker_hub",
                arguments="--nv",
            )
            containers.append(cf_container)

            colabfold_batch = Transformation(
                "colabfold_batch",
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, "bin/colabfold_batch.py"),
                is_stageable=True,
                container=cf_container,
            ).add_pegasus_profile(
                memory=TOOL_CONFIGS["colabfold_batch"]["memory"],
                cores=TOOL_CONFIGS["colabfold_batch"]["cores"],
                gpus=1,
            ).add_profiles(
                Namespace.CONDOR,
                key="transfer_input_files",
                value=args.colabfold_cache_dir,
            )
            transformations.append(colabfold_batch)

        elif args.mode == "alphafold3":
            af3_container = Container(
                "alphafold3",
                container_type=Container.SINGULARITY,
                image="docker://kthare10/alphafold3:pegasus",
                image_site="docker_hub",
                arguments="--nv",
            )
            containers.append(af3_container)

            # Build transfer_input_files value for CondorIO
            af3_transfer_dirs = [args.db_dir]
            if args.model_dir != args.db_dir:
                af3_transfer_dirs.append(args.model_dir)
            af3_transfer_value = ",".join(af3_transfer_dirs)

            af3_msa = Transformation(
                "af3_msa",
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, "bin/af3_msa.py"),
                is_stageable=True,
                container=af3_container,
            ).add_pegasus_profile(
                memory=TOOL_CONFIGS["af3_msa"]["memory"],
                cores=TOOL_CONFIGS["af3_msa"]["cores"],
            ).add_profiles(
                Namespace.CONDOR,
                key="transfer_input_files",
                value=af3_transfer_value,
            )
            transformations.append(af3_msa)

            af3_fold = Transformation(
                "af3_fold",
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, "bin/af3_fold.py"),
                is_stageable=True,
                container=af3_container,
            ).add_pegasus_profile(
                memory=TOOL_CONFIGS["af3_fold"]["memory"],
                cores=TOOL_CONFIGS["af3_fold"]["cores"],
                gpus=1,
            ).add_profiles(
                Namespace.CONDOR,
                key="transfer_input_files",
                value=af3_transfer_value,
            )
            transformations.append(af3_fold)

        elif args.mode == "boltz":
            boltz_container = Container(
                "boltz",
                container_type=Container.SINGULARITY,
                image="docker://kthare10/boltz:pegasus",
                image_site="docker_hub",
                arguments="--nv",
            )
            containers.append(boltz_container)

            boltz_predict = Transformation(
                "boltz_predict",
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, "bin/boltz_predict.py"),
                is_stageable=True,
                container=boltz_container,
            ).add_pegasus_profile(
                memory=TOOL_CONFIGS["boltz_predict"]["memory"],
                cores=TOOL_CONFIGS["boltz_predict"]["cores"],
                gpus=1,
            ).add_profiles(
                Namespace.CONDOR,
                key="transfer_input_files",
                value=args.boltz_cache_dir,
            )
            transformations.append(boltz_predict)

        self.tc.add_containers(*containers)
        self.tc.add_transformations(*transformations)

    # ------------------------------------------------------------------
    # Replica Catalog
    # ------------------------------------------------------------------
    def create_replica_catalog(self, args):
        self.rc = ReplicaCatalog()

        # Register the input TSV file
        self.rc.add_replica(
            "local",
            os.path.basename(args.input),
            "file://" + os.path.abspath(args.input),
        )

        # Register any FASTA files referenced in the TSV
        fasta_refs = find_fasta_references(args.input)
        tsv_dir = os.path.dirname(os.path.abspath(args.input))
        for fasta_ref in fasta_refs:
            if os.path.isabs(fasta_ref):
                abs_path = fasta_ref
            else:
                abs_path = os.path.abspath(os.path.join(tsv_dir, fasta_ref))
            if os.path.exists(abs_path):
                self.rc.add_replica(
                    "local", fasta_ref, "file://" + abs_path
                )
            else:
                logger.warning(f"FASTA file not found: {abs_path}")

    # ------------------------------------------------------------------
    # Workflow DAG
    # ------------------------------------------------------------------
    def create_workflow(self, args):
        """Create the workflow DAG based on the selected mode."""
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        if args.mode == "colabfold":
            self._create_colabfold_workflow(args)
        elif args.mode == "alphafold3":
            self._create_alphafold3_workflow(args)
        elif args.mode == "boltz":
            self._create_boltz_workflow(args)

    # --- ColabFold mode ---
    def _create_colabfold_workflow(self, args):
        """Build DAG: process_tsv -> [colabfold_batch per pair] -> rank_af.

        Nextflow equivalent: workflows/colabfold.nf
        """
        tsv_dir = os.path.dirname(os.path.abspath(args.input))
        output_filenames = compute_output_filenames(
            args.input, "colabfold", workdir=tsv_dir
        )
        logger.info(f"ColabFold: {len(output_filenames)} bait-prey combinations")

        # --- Step 1: process_tsv (PROCESS_TSV in Nextflow) ---
        tsv_file = File(os.path.basename(args.input))
        fasta_refs = find_fasta_references(args.input)
        fasta_input_files = [File(ref) for ref in fasta_refs]
        output_fasta_files = [File(fn) for fn in output_filenames]

        process_tsv_job = (
            Job("process_tsv", _id="process_tsv", node_label="process_tsv")
            .add_args(
                "--output-dir", ".",
                "--workdir", ".",
                "--mode", "colabfold",
                tsv_file,
            )
            .add_inputs(tsv_file, *fasta_input_files)
        )
        for f in output_fasta_files:
            process_tsv_job.add_outputs(f, stage_out=True, register_replica=False)
        self.wf.add_jobs(process_tsv_job)

        # --- Step 2: colabfold_batch per pair (COLABFOLD_BATCH in Nextflow) ---
        toprank_json_files = []

        # Build extra args string from colabfold-specific params
        extra_args_parts = []
        if args.host_url:
            extra_args_parts.append(f"--host-url {args.host_url}")
        if args.colabfold_args:
            extra_args_parts.append(args.colabfold_args)
        extra_args_str = " ".join(extra_args_parts)

        for fasta_file in output_fasta_files:
            acc_id = os.path.splitext(fasta_file.lfn)[0]
            toprank_json = File(f"{acc_id}_toprank.json")
            toprank_json_files.append(toprank_json)

            cf_job = (
                Job("colabfold_batch", _id=f"cf_{acc_id}", node_label=f"cf_{acc_id}")
                .add_args(
                    "--input", fasta_file,
                    "--output-json", toprank_json,
                    "--num-recycle", str(args.num_recycle),
                    "--cache-dir", os.path.basename(args.colabfold_cache_dir.rstrip("/")),
                )
                .add_inputs(fasta_file)
                .add_outputs(toprank_json, stage_out=True, register_replica=False)
                .add_pegasus_profiles(label=acc_id)
            )
            if extra_args_str:
                cf_job.add_args("--extra-args", f'"{extra_args_str}"')
            self.wf.add_jobs(cf_job)

        # --- Step 3: rank_af (RANK_AF in Nextflow) ---
        ranked_tsv = File("colabfold_ranked_results.tsv")
        rank_job = (
            Job("rank_af", _id="rank_af", node_label="rank_af")
            .add_args(
                "--output", "colabfold_ranked_results.tsv",
                "--mode", "colabfold",
            )
            .add_inputs(*toprank_json_files)
            .add_outputs(ranked_tsv, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(rank_job)

        logger.info(
            f"  Jobs: 1 process_tsv + {len(output_fasta_files)} colabfold_batch + 1 rank_af"
        )

    # --- AlphaFold3 mode ---
    def _create_alphafold3_workflow(self, args):
        """Build DAG: process_tsv -> [af3_msa per pair] -> [af3_fold per batch] -> rank_af.

        Nextflow equivalent: workflows/alphafold3.nf

        Note: db-dir and model-dir are transferred via CondorIO
        (transfer_input_files) rather than bind mounts. The wrapper
        scripts reference the transferred directory basenames.
        """
        tsv_dir = os.path.dirname(os.path.abspath(args.input))
        output_filenames = compute_output_filenames(
            args.input, "alphafold3", workdir=tsv_dir
        )
        logger.info(f"AlphaFold3: {len(output_filenames)} bait-prey combinations")

        # --- Step 1: process_tsv (PROCESS_TSV in Nextflow) ---
        tsv_file = File(os.path.basename(args.input))
        fasta_refs = find_fasta_references(args.input)
        fasta_input_files = [File(ref) for ref in fasta_refs]
        output_json_files = [File(fn) for fn in output_filenames]

        process_tsv_job = (
            Job("process_tsv", _id="process_tsv", node_label="process_tsv")
            .add_args(
                "--output-dir", ".",
                "--workdir", ".",
                "--mode", "alphafold3",
                tsv_file,
            )
            .add_inputs(tsv_file, *fasta_input_files)
        )
        for f in output_json_files:
            process_tsv_job.add_outputs(f, stage_out=True, register_replica=False)
        self.wf.add_jobs(process_tsv_job)

        # --- Step 2: af3_msa per JSON (AF3_MSA in Nextflow) ---
        # db-dir is transferred via CondorIO; use basename in args
        db_basename = os.path.basename(args.db_dir.rstrip("/"))
        model_basename = os.path.basename(args.model_dir.rstrip("/"))

        msa_json_files = []
        for json_file in output_json_files:
            name = os.path.splitext(json_file.lfn)[0]
            msa_json = File(f"{name}_data.json")
            msa_json_files.append(msa_json)

            msa_job = (
                Job("af3_msa", _id=f"msa_{name}", node_label=f"msa_{name}")
                .add_args(
                    "--input", json_file,
                    "--output", msa_json,
                    "--db-dir", db_basename,
                )
                .add_inputs(json_file)
                .add_outputs(msa_json, stage_out=True, register_replica=False)
                .add_pegasus_profiles(label=name)
            )
            self.wf.add_jobs(msa_job)

        # --- Step 3: af3_fold per batch (AF3_FOLD in Nextflow) ---
        # Nextflow uses .collate(params.inf_batch) to batch MSA JSONs
        confidence_json_files = []
        num_batches = 0

        for batch_idx, batch in enumerate(_batched(msa_json_files, args.inf_batch)):
            num_batches += 1
            batch_confidence_jsons = []

            for msa_file in batch:
                name = msa_file.lfn.replace("_data.json", "")
                conf_json = File(f"{name}_summary_confidences.json")
                batch_confidence_jsons.append(conf_json)
                confidence_json_files.append(conf_json)

            fold_job = (
                Job("af3_fold", _id=f"fold_b{batch_idx}", node_label=f"fold_b{batch_idx}")
                .add_args(
                    "--db-dir", db_basename,
                    "--model-dir", model_basename,
                )
            )

            # Add extra AF3 arguments
            extra_args_parts = []
            if args.max_template_date:
                extra_args_parts.append(f"--max_template_date={args.max_template_date}")
            if args.num_recycles:
                extra_args_parts.append(f"--num_recycles={args.num_recycles}")
            if args.af3_args:
                extra_args_parts.append(args.af3_args)
            if extra_args_parts:
                fold_job.add_args("--extra-args", '"{}"'.format(" ".join(extra_args_parts)))

            # Add batch inputs
            for msa_file in batch:
                fold_job.add_args("--input", msa_file)
                fold_job.add_inputs(msa_file)

            # Add batch outputs
            for conf_json in batch_confidence_jsons:
                fold_job.add_outputs(conf_json, stage_out=True, register_replica=False)

            self.wf.add_jobs(fold_job)

        # --- Step 4: rank_af (RANK_AF in Nextflow) ---
        ranked_tsv = File("alphafold3_ranked_results.tsv")
        rank_job = (
            Job("rank_af", _id="rank_af", node_label="rank_af")
            .add_args(
                "--output", "alphafold3_ranked_results.tsv",
                "--mode", "alphafold3",
            )
            .add_inputs(*confidence_json_files)
            .add_outputs(ranked_tsv, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(rank_job)

        logger.info(
            f"  Jobs: 1 process_tsv + {len(msa_json_files)} af3_msa "
            f"+ {num_batches} af3_fold + 1 rank_af"
        )

    # --- Boltz mode ---
    def _create_boltz_workflow(self, args):
        """Build DAG: process_tsv -> [boltz_predict per batch] -> rank_af.

        Nextflow equivalent: workflows/boltz.nf
        """
        tsv_dir = os.path.dirname(os.path.abspath(args.input))
        output_filenames = compute_output_filenames(
            args.input, "boltz", workdir=tsv_dir
        )
        logger.info(f"Boltz: {len(output_filenames)} bait-prey combinations")

        # --- Step 1: process_tsv (PROCESS_TSV in Nextflow) ---
        tsv_file = File(os.path.basename(args.input))
        fasta_refs = find_fasta_references(args.input)
        fasta_input_files = [File(ref) for ref in fasta_refs]
        output_fasta_files = [File(fn) for fn in output_filenames]

        process_tsv_job = (
            Job("process_tsv", _id="process_tsv", node_label="process_tsv")
            .add_args(
                "--output-dir", ".",
                "--workdir", ".",
                "--mode", "boltz",
                tsv_file,
            )
            .add_inputs(tsv_file, *fasta_input_files)
        )
        for f in output_fasta_files:
            process_tsv_job.add_outputs(f, stage_out=True, register_replica=False)
        self.wf.add_jobs(process_tsv_job)

        # --- Step 2: boltz_predict per batch (BOLTZ_PREDICT in Nextflow) ---
        # Nextflow uses .collate(params.inf_batch) to batch FASTA files
        confidence_json_files = []
        num_batches = 0

        # Build extra args string
        extra_args_parts = []
        if args.use_msa_server:
            extra_args_parts.append("--use_msa_server")
        if args.msa_server_url:
            extra_args_parts.append(f"--msa_server_url={args.msa_server_url}")
        if args.boltz_args:
            extra_args_parts.append(args.boltz_args)
        extra_args_str = " ".join(extra_args_parts)

        for batch_idx, batch in enumerate(_batched(output_fasta_files, args.inf_batch)):
            num_batches += 1
            batch_confidence_jsons = []

            for fasta_file in batch:
                name = os.path.splitext(fasta_file.lfn)[0]
                conf_json = File(f"{name}_confidence.json")
                batch_confidence_jsons.append(conf_json)
                confidence_json_files.append(conf_json)

            predict_job = (
                Job("boltz_predict", _id=f"predict_b{batch_idx}", node_label=f"predict_b{batch_idx}")
                .add_args("--cache-dir", os.path.basename(args.boltz_cache_dir.rstrip("/")))
            )

            if extra_args_str:
                predict_job.add_args("--extra-args", f'"{extra_args_str}"')

            # Add batch inputs
            for fasta_file in batch:
                predict_job.add_args("--input", fasta_file)
                predict_job.add_inputs(fasta_file)

            # Add batch outputs
            for conf_json in batch_confidence_jsons:
                predict_job.add_outputs(conf_json, stage_out=True, register_replica=False)

            self.wf.add_jobs(predict_job)

        # --- Step 3: rank_af (RANK_AF in Nextflow) ---
        ranked_tsv = File("boltz_ranked_results.tsv")
        rank_job = (
            Job("rank_af", _id="rank_af", node_label="rank_af")
            .add_args(
                "--output", "boltz_ranked_results.tsv",
                "--mode", "boltz",
            )
            .add_inputs(*confidence_json_files)
            .add_outputs(ranked_tsv, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(rank_job)

        logger.info(
            f"  Jobs: 1 process_tsv + {num_batches} boltz_predict + 1 rank_af"
        )


# ======================================================================
# main()
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Pegasus workflow generator for chienlab-proteinfold",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # ColabFold mode
  %(prog)s --mode colabfold --input acclist.tsv \\
      --colabfold-cache-dir /path/to/cache

  # AlphaFold3 mode
  %(prog)s --mode alphafold3 --input acclist.tsv \\
      --db-dir /path/to/af3db --model-dir /path/to/models

  # Boltz mode
  %(prog)s --mode boltz --input acclist.tsv \\
      --model boltz2 --boltz-cache-dir /path/to/cache
""",
    )

    # --- Standard Pegasus arguments ---
    parser.add_argument(
        "-s", "--skip-sites-catalog", action="store_true",
        help="Skip site catalog creation",
    )
    parser.add_argument(
        "-e", "--execution-site-name", default="condorpool",
        help="Execution site name (default: condorpool)",
    )
    parser.add_argument(
        "-o", "--output", default="workflow.yml",
        help="Output file (default: workflow.yml)",
    )

    # --- Common arguments ---
    parser.add_argument(
        "--mode", required=True, choices=["colabfold", "alphafold3", "boltz"],
        help="Prediction mode: colabfold, alphafold3, or boltz",
    )
    parser.add_argument(
        "--input", required=True,
        help="Input TSV file with Entry and bait columns",
    )
    parser.add_argument(
        "--inf-batch", type=int, default=20,
        help="Batch size for GPU inference jobs (default: 20)",
    )

    # --- ColabFold arguments ---
    cf_group = parser.add_argument_group("ColabFold options")
    cf_group.add_argument(
        "--num-recycle", type=int, default=3,
        help="Number of recycles (default: 3)",
    )
    cf_group.add_argument(
        "--colabfold-cache-dir",
        help="Path to ColabFold cache directory (transferred via HTCondor)",
    )
    cf_group.add_argument(
        "--colabfold-args", default="",
        help="Extra arguments passed to colabfold_batch",
    )
    cf_group.add_argument(
        "--host-url", default=None,
        help="MSA server URL (default: None)",
    )

    # --- AlphaFold3 arguments ---
    af3_group = parser.add_argument_group("AlphaFold3 options")
    af3_group.add_argument(
        "--db-dir",
        help="AlphaFold3 database directory (transferred via HTCondor)",
    )
    af3_group.add_argument(
        "--model-dir",
        help="AlphaFold3 model parameters directory (transferred via HTCondor)",
    )
    af3_group.add_argument(
        "--max-template-date", default="2021-09-30",
        help="Max template date (default: 2021-09-30)",
    )
    af3_group.add_argument(
        "--num-recycles", type=int, default=10,
        help="Number of recycles for AF3 inference (default: 10)",
    )
    af3_group.add_argument(
        "--af3-args", default="",
        help="Extra arguments passed to run_alphafold.py",
    )

    # --- Boltz arguments ---
    boltz_group = parser.add_argument_group("Boltz options")
    boltz_group.add_argument(
        "--model", default="boltz2", choices=["boltz1", "boltz2"],
        help="Boltz model version (default: boltz2)",
    )
    boltz_group.add_argument(
        "--boltz-cache-dir",
        help="Boltz cache directory (transferred via HTCondor)",
    )
    boltz_group.add_argument(
        "--use-msa-server", action=argparse.BooleanOptionalAction, default=False,
        help="Use MSA server for Boltz (--use-msa-server / --no-use-msa-server, default: false)",
    )
    boltz_group.add_argument(
        "--msa-server-url", default="http://cfold-db:8888",
        help="MSA server URL for Boltz (default: http://cfold-db:8888)",
    )
    boltz_group.add_argument(
        "--boltz-args", default="",
        help="Extra arguments passed to boltz predict",
    )

    args = parser.parse_args()

    # --- Validate mode-specific arguments ---
    if args.mode == "colabfold" and not args.colabfold_cache_dir:
        parser.error("--colabfold-cache-dir is required for colabfold mode")
    if args.mode == "alphafold3":
        if not args.db_dir:
            parser.error("--db-dir is required for alphafold3 mode")
        if not args.model_dir:
            parser.error("--model-dir is required for alphafold3 mode")
    if args.mode == "boltz" and not args.boltz_cache_dir:
        parser.error("--boltz-cache-dir is required for boltz mode")

    # Validate input file
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("PROTEINFOLD WORKFLOW GENERATOR (Pegasus)")
    logger.info("=" * 70)
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Input: {args.input}")
    logger.info(f"Batch size: {args.inf_batch}")
    logger.info(f"Execution site: {args.execution_site_name}")
    logger.info(f"Output: {args.output}")
    logger.info("=" * 70)

    try:
        workflow = ProteinfoldWorkflow(dagfile=args.output)

        workflow.create_pegasus_properties()

        if not args.skip_sites_catalog:
            workflow.create_sites_catalog(
                exec_site_name=args.execution_site_name
            )

        workflow.create_transformation_catalog(
            args, exec_site_name=args.execution_site_name
        )
        workflow.create_replica_catalog(args)
        workflow.create_workflow(args)
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
