#!/usr/bin/env python3

from pathlib import Path
import os
import datetime as dt
from typing import Dict, List, Tuple
import argparse

from Pegasus.api import *
from support_code import filename_utilities

# ==============================================================================
# HELPERS
# ==============================================================================

from typing import Union

def base(f: Union[str, Path]) -> str:
    return os.path.basename(str(f))


def carve_up_times(stoptime: str, ndays: int, subrange: int = 1) -> List[Tuple[str, str]]:
    """
    Split lookback window into smaller subranges for parallelism.
    Returns list of (stoptime_string, ndays_string) tuples.
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    stopstamp = dt.datetime.strptime(stoptime, fmt)
    startstamp = stopstamp - dt.timedelta(days=ndays)

    ranges = []
    current = startstamp
    while current < stopstamp:
        end = min(current + dt.timedelta(days=subrange), stopstamp)
        ranges.append((end.strftime(fmt), str(-subrange)))
        current = end
    return ranges


# ==============================================================================
# CATALOG BUILDERS
# ==============================================================================

def build_site_catalog(top_dir: Path) -> SiteCatalog:
    """Build Pegasus SiteCatalog with local scratch/output and condorpool sites."""
    scratch_dir, output_dir = top_dir / "scratch", top_dir / "output"
    scratch_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    sc = SiteCatalog()

    local = (
        Site("local")
        .add_directories(
            Directory(Directory.SHARED_SCRATCH, str(scratch_dir))
            .add_file_servers(FileServer(f"file://{scratch_dir}", Operation.ALL)),
            Directory(Directory.LOCAL_STORAGE, str(output_dir))
            .add_file_servers(FileServer(f"file://{output_dir}", Operation.ALL)),
        )
        .add_profiles(
            Namespace.PEGASUS,
            key="SSH_PRIVATE_KEY",
            value="/home/jtilson/.ssh/id_rsa",
        )
    )

    condorpool = (
        Site("condorpool")
        .add_pegasus_profile(style="condor", data_configuration="condorio")
        .add_condor_profile(universe="vanilla")
        #.add_condor_profile(requirements="TestPool =!= True && OSPool =!= True")
    )

    sc.add_sites(local, condorpool)
    return sc


def build_transformation_catalog() -> TransformationCatalog:
    """Build TransformationCatalog with containerized tasks."""
    tc = TransformationCatalog()

    container = Container(
        "unet_wf_model",
        Container.SINGULARITY,
        image="docker://containers.renci.org/eds/ast_run_harvester:v0.0.24",
        image_site="docker_hub",
    )

    execute = (
        Transformation(
            "execute_run_obs_harvester",
            container=container,
            site="local",
            pfn=f"{TOP_DIR}/support_code/run_fetch_pegasus_observation_class.py",
            is_stageable=True,
        )
        .add_pegasus_profile(memory="2 GB")
    )

    merge = (
        Transformation(
            "merge_utilities",
            container=container,
            site="local",
            pfn=f"{TOP_DIR}/support_code/merge_utilities.py",
            is_stageable=True,
        )
        .add_pegasus_profile(memory="4 GB")
    )

    tc.add_containers(container)
    tc.add_transformations(execute, merge)
    return tc


# ==============================================================================
# JOB BUILDERS
# ==============================================================================

def build_harvest_job(
    stoptime: str,
    ndays: str,
    source_yaml: str,
    source_csv: str,
    datafs: List[str],
    metafs: List[str],
    final_dir: str,
    sampling_min: int,
    noaa_datum: str,
    include_main: bool = True,
    include_contrails: bool = False,
) -> Job:
    """Construct a harvest job."""
    job = Job("execute_run_obs_harvester").add_args(
        "--noaa_datum", noaa_datum,
        "--sampling_min", sampling_min,
        "--stoptime", f'"{stoptime}"',
        "--map_source_file", base(source_yaml),
        "--ndays", ndays,
        "--finalDIR", final_dir,
        )

    if include_contrails:
        job.add_args('--contrails_auth',base(SOURCE_CONTRAILS))
    inputs = [base(source_yaml), base(source_csv)]
    if include_contrails:
        inputs.append(base(SOURCE_CONTRAILS))
    if include_main:
        inputs.append(base(SOURCE_MAIN))

    job.add_inputs(*inputs)

    for f in datafs + metafs:
        job.add_outputs(f, stage_out=False)

    return job


def build_merge_job(bstr: str, files: List[str], endt: str, include_main: bool = True) -> Job:
    """Construct a merge job."""
    outname = f"full_{bstr}_{endt}.csv"
    job = Job("merge_utilities").add_args("--outfilename", outname, "--obs_filelist", *files)

    inputs = list(files)
    if include_main:
        inputs.append(base(SOURCE_MAIN))
    job.add_inputs(*inputs)
    job.add_outputs(outname, stage_out=True)
    return job


# ==============================================================================
# CLI PARSER
# ==============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="Build a Pegasus workflow to harvest observation data in parallel time chunks."
    )
    p.add_argument("--stoptime", required=True, help='Stop time, e.g. "2026-02-10 00:00:00"')
    p.add_argument("--ndays", default=5, type=int, help="Lookback days (>=0)")
    p.add_argument("--subrange_days", default=1, type=int, help="Days per parallel sub-job (default: 1)")
    p.add_argument("--yaml_dir", required=True, help="Directory containing source YAMLs")
    p.add_argument("--source_main", default="", help="Mandatory main.yml file")
    p.add_argument("--source_contrails", default="", help="Optional contrails.yml file")
    p.add_argument("--final_dir", default="./", help="finalDIR passed to harvester (default: ./)")
    p.add_argument("--noaa_datum", default="MSL", help="NOAA datum (default: MSL)")
    p.add_argument("--sampling_min", default=15, type=int, help="Sampling minutes (default: 15)")
    p.add_argument("--workflow_name", default="obs_workflow", help="Pegasus workflow name")
    p.add_argument("--workflow_out", default="clean_refactor_parallel_splittimes_workflow.yml",
                   help="Output workflow YAML filename")
    p.add_argument("--top_dir", default=".", help="Directory used to create scratch/ and output/")
    return p.parse_args()


# ==============================================================================
# MAIN WORKFLOW
# ==============================================================================

def main():
    global TOP_DIR
    global SOURCE_MAIN
    global SOURCE_CONTRAILS

    args = parse_args()

    # Validate
    if args.ndays < 0:
        raise ValueError(f"--ndays must be >= 0, got {args.ndays}")

    YAML_DIR = Path(args.yaml_dir).expanduser().resolve()
    if not YAML_DIR.exists():
        raise FileNotFoundError(f"--yaml_dir does not exist: {YAML_DIR}")

    SOURCE_MAIN = Path(args.source_main.strip() or "__MISSING__").expanduser().resolve()
    include_main = SOURCE_MAIN.exists()
    if not include_main:
        print(f"Warning: SOURCE_MAIN '{SOURCE_MAIN}' not found. Skipping it in job inputs.")

    SOURCE_CONTRAILS = Path(args.source_contrails.strip() or "__MISSING__").expanduser().resolve()
    include_contrails = SOURCE_CONTRAILS.exists()
    if not include_contrails:
        print(f"Warning: SOURCE_CONTRAILS '{SOURCE_CONTRAILS}' not found. Skipping it in job inputs.")

    FINAL_DIR = args.final_dir
    STOPTIME = args.stoptime
    NDAYS = args.ndays
    SUBRANGE_DAYS = args.subrange_days
    NOAA_DATUM = args.noaa_datum
    SAMPLING_MIN = args.sampling_min
    TOP_DIR = Path(args.top_dir).expanduser().resolve()

    # Time chunks for parallelism
    stoptime_pairs = carve_up_times(STOPTIME, NDAYS, SUBRANGE_DAYS)

    # Workflow and ReplicaCatalog
    wf = Workflow("obs_workflow")
    rc = ReplicaCatalog()

    if include_main:
        rc.add_replica("local", base(SOURCE_MAIN), str(SOURCE_MAIN))
    if include_contrails:
        rc.add_replica("local", base(SOURCE_CONTRAILS), str(SOURCE_CONTRAILS))

    yaml_files = filename_utilities.return_list_files(YAML_DIR, ext="yaml")

    for in_yaml in yaml_files:
        source_yaml, source_csv = filename_utilities.return_inputs_yaml_stationnames(YAML_DIR, in_yaml)

        rc.add_replica("local", base(source_yaml), source_yaml)
        rc.add_replica("local", base(source_csv), source_csv)

        file_producers: Dict[str, Job] = {}
        all_datafs: List[str] = []
        all_metafs: List[str] = []
        bstrings = []

        # --------------------------
        # HARVEST JOBS
        # --------------------------
        for stoptime, nd in stoptime_pairs:
            datafs, metafs, bstrings = filename_utilities.return_list_outputfilenames(stoptime, source_yaml)
            all_datafs.extend(datafs)
            all_metafs.extend(metafs)

            harvest_job = build_harvest_job(
                stoptime, nd, source_yaml, source_csv,
                datafs, metafs,
                FINAL_DIR, SAMPLING_MIN, NOAA_DATUM, include_main, include_contrails
            )
            wf.add_jobs(harvest_job)

            # Track file -> job for dependencies
            for f in datafs + metafs:
                file_producers[f] = harvest_job

        # --------------------------
        # MERGE JOBS
        # --------------------------
        endt = STOPTIME.replace(" ", "T")

        for bstr in bstrings:
            files = [f for f in (all_metafs if "meta" in bstr else all_datafs) if bstr in f]
            if not files:
                continue

            merge_job = build_merge_job(bstr, files, endt, include_main)
            wf.add_jobs(merge_job)

            # Add dependencies
            parents = {file_producers[f] for f in files if f in file_producers}
            wf.add_dependency(merge_job, parents=list(parents))

    # --------------------------
    # ADD CATALOGS
    # --------------------------
    wf.add_site_catalog(build_site_catalog(TOP_DIR))
    wf.add_replica_catalog(rc)
    wf.add_transformation_catalog(build_transformation_catalog())

    # Write workflow
    wf.write("parallel_splittimes_workflow.yml")
    print("Workflow written to parallel_splittimes_workflow.yml")

if __name__ == "__main__":
    main()
