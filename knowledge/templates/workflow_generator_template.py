#!/usr/bin/env python3

"""
Pegasus workflow generator for [WORKFLOW_NAME].

[CUSTOMIZE] Describe the pipeline and what it does.

Pipeline steps:
1. [step1] - [description]
2. [step2] - [description]
3. [step3] - [description]

Usage:
    ./workflow_generator.py [CUSTOMIZE: add usage examples]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from Pegasus.api import *

# [CUSTOMIZE] Add any additional imports needed for your workflow
# Examples: json, csv, glob, datetime, requests, urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# [CUSTOMIZE] Per-tool resource configuration (see mag-workflow, earthquake-workflow)
# Maps tool names to memory and core requirements for transformation catalog.
TOOL_CONFIGS = {
    "step1": {"memory": "2 GB", "cores": 1},
    "step2": {"memory": "4 GB", "cores": 2},
    "step3": {"memory": "2 GB", "cores": 1},
    # "merge": {"memory": "1 GB", "cores": 1},
}


class MyWorkflow:
    """[CUSTOMIZE] Describe your workflow class."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "my_workflow"  # [CUSTOMIZE] Workflow name

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
    # Properties — rarely needs changes
    # ------------------------------------------------------------------
    def create_pegasus_properties(self):
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"
        # [CUSTOMIZE] Add any extra properties if needed

    # ------------------------------------------------------------------
    # Site Catalog — standard boilerplate
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
    def create_transformation_catalog(self, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()

        # Container definition
        container = Container(
            "my_container",  # [CUSTOMIZE] Container name
            container_type=Container.SINGULARITY,
            image="docker://username/image:latest",  # [CUSTOMIZE] Image
            image_site="docker_hub",
        )

        # [CUSTOMIZE] Register each wrapper script as a transformation.
        #
        # Pattern A: Stageable scripts on the submit host (most workflows).
        #   See: tnseq-workflow, earthquake-workflow, soilmoisture-workflow
        #
        #   tx = Transformation(
        #       "step_name",
        #       site=exec_site_name,
        #       pfn=os.path.join(self.wf_dir, "bin/step_name.py"),
        #       is_stageable=True,
        #       container=container,
        #   ).add_pegasus_profile(memory="2 GB", cores=1)
        #
        # Pattern B: Scripts baked into the container (is_stageable=False).
        #   See: mag-workflow
        #
        #   tx = Transformation(
        #       "step_name",
        #       site="local",
        #       pfn="/usr/local/bin/step_name.sh",
        #       is_stageable=False,
        #       container=container,
        #   ).add_pegasus_profile(memory="4 GB", cores=2)

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

        # [CUSTOMIZE] Add mkdir if you need local directory creation
        # mkdir = Transformation(
        #     "mkdir", site="local", pfn="/bin/mkdir", is_stageable=False
        # )
        # transformations.append(mkdir)

        self.tc.add_containers(container)
        self.tc.add_transformations(*transformations)

    # ------------------------------------------------------------------
    # Replica Catalog
    # ------------------------------------------------------------------
    def create_replica_catalog(self):
        self.rc = ReplicaCatalog()

        # [CUSTOMIZE] Register input files.
        #
        # Pattern A: Local data files (tnseq-workflow)
        #   for sample in self.samples:
        #       path = os.path.join(self.data_dir, f"{sample}.fq.gz")
        #       self.rc.add_replica("local", f"{sample}.fq.gz",
        #                           "file://" + os.path.abspath(path))
        #
        # Pattern B: Support scripts called by wrappers (tnseq-workflow)
        #   jar_path = os.path.join(self.wf_dir, "bin/tool.jar")
        #   self.rc.add_replica("local", "tool.jar", "file://" + jar_path)
        #
        # Pattern C: No input files — first job fetches from API (earthquake-workflow)
        #   pass
        #
        # Pattern D: Config/catalog file generated at workflow creation time (airquality)
        #   self.rc.add_replica("local", "catalog.csv",
        #                       "file://" + os.path.join(self.wf_dir, "catalog.csv"))

    # ------------------------------------------------------------------
    # Workflow DAG
    # ------------------------------------------------------------------
    def create_workflow(self, args):
        """Create the workflow DAG.

        [CUSTOMIZE] Choose the right dependency mode:
          - Workflow(name, infer_dependencies=True)  — recommended for most
          - Workflow(name)  + explicit add_dependency() — when needed
        """
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        # [CUSTOMIZE] Choose your iteration pattern:
        #
        # Pattern A: Per-sample parallelism (tnseq-workflow)
        #   for sample in self.samples:
        #       self._add_sample_pipeline(sample)
        #
        # Pattern B: Per-region parallelism (earthquake-workflow, airquality)
        #   for region in args.regions:
        #       self._add_region_pipeline(region)
        #
        # Pattern C: Per-polygon / per-location (soilmoisture-workflow)
        #   for polygon_id in args.polygon_ids:
        #       self._add_polygon_pipeline(polygon_id)
        #
        # Pattern D: Single linear pipeline (simple workflows)
        #   self._add_pipeline(args)

        # Example: per-item parallelism
        for item in args.items:
            self._add_item_pipeline(item, args)

        # [CUSTOMIZE] Optional fan-in merge step (tnseq, airquality)
        # if len(result_files) > 1:
        #     file_args = " ".join([f"-i {f.lfn}" for f in result_files])
        #     merge_job = (
        #         Job("merge", _id="merge_all")
        #         .add_args(f"{file_args} -o merged_results.json")
        #         .add_inputs(*result_files)
        #         .add_outputs(merged, stage_out=True, register_replica=False)
        #     )
        #     self.wf.add_jobs(merge_job)

    def _add_item_pipeline(self, item, args):
        """Add jobs for a single item.

        [CUSTOMIZE] Replace with your actual pipeline logic.
        """
        # Output file declarations
        output1 = File(f"{item}_step1_output.csv")
        output2 = File(f"{item}_step2_output.json")
        output3 = File(f"{item}_step3_result.png")

        # Job 1: First step
        job1 = (
            Job("step1", _id=f"step1_{item}", node_label=f"step1_{item}")
            .add_args("--input", "input_data.csv", "--output", output1)
            # .add_inputs(input_file)  # [CUSTOMIZE] Add actual inputs
            .add_outputs(output1, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=item)
        )
        self.wf.add_jobs(job1)

        # Job 2: Second step — depends on Job 1 via shared File object
        job2 = (
            Job("step2", _id=f"step2_{item}", node_label=f"step2_{item}")
            .add_args("--input", output1, "--output", output2)
            .add_inputs(output1)
            .add_outputs(output2, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=item)
        )
        self.wf.add_jobs(job2)

        # Job 3: Final step — stage_out=True for user-facing results
        job3 = (
            Job("step3", _id=f"step3_{item}", node_label=f"step3_{item}")
            .add_args("--input", output2, "--output", output3)
            .add_inputs(output2)
            .add_outputs(output3, stage_out=True, register_replica=False)
            .add_pegasus_profiles(label=item)
        )
        self.wf.add_jobs(job3)


# ======================================================================
# main() — CLI argument parsing
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="[CUSTOMIZE] Workflow description",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --items foo bar --output workflow.yml
  %(prog)s --items foo --skip-sites-catalog -e condorpool
""",
    )

    # --- Standard Pegasus arguments (keep these) ---
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

    # --- [CUSTOMIZE] Workflow-specific arguments ---
    #
    # Pattern A: Explicit items (earthquake --regions, soilmoisture --polygon-ids)
    parser.add_argument(
        "--items",
        type=str,
        nargs="+",
        required=True,
        help="Items to process in parallel",
    )

    # Pattern B: Samplesheet input (mag-workflow)
    # parser.add_argument("--samplesheet", type=str, help="CSV samplesheet")

    # Pattern C: Test mode with auto-download (mag-workflow)
    # parser.add_argument("--test", action="store_true",
    #                     help="Download test data and run with minimal settings")

    # Pattern D: Date range (earthquake, soilmoisture, airquality)
    # parser.add_argument("--start-date", type=str, required=True)
    # parser.add_argument("--end-date", type=str, default=None)

    # Pattern E: Skip flags for conditional DAG (mag, airquality)
    # parser.add_argument("--skip-step2", action="store_true")
    # parser.add_argument("--skip-step3", action="store_true")

    args = parser.parse_args()

    # --- [CUSTOMIZE] Input validation ---
    # if not args.test and not args.samplesheet:
    #     print("Error: Either --test or --samplesheet must be provided")
    #     sys.exit(1)

    logger.info("=" * 70)
    logger.info("MY WORKFLOW GENERATOR")  # [CUSTOMIZE]
    logger.info("=" * 70)
    logger.info(f"Items: {args.items}")
    logger.info(f"Execution site: {args.execution_site_name}")
    logger.info(f"Output file: {args.output}")
    logger.info("=" * 70)

    try:
        workflow = MyWorkflow(dagfile=args.output)

        workflow.create_pegasus_properties()

        if not args.skip_sites_catalog:
            workflow.create_sites_catalog(
                exec_site_name=args.execution_site_name
            )

        workflow.create_transformation_catalog(
            exec_site_name=args.execution_site_name
        )
        workflow.create_replica_catalog()
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
