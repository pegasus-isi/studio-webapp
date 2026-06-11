#!/usr/bin/env python3

"""
Soil Moisture Workflow Generator for Pegasus WMS.

Creates a workflow for:
1. Fetching soil moisture data from Open-Meteo
2. Analyzing moisture levels with crop-specific thresholds
3. Predicting irrigation needs using ML/rule-based methods
4. Generating visualizations and recommendations

Supports standard single-site execution and edge-to-cloud DPU architecture.

Usage:
    # Open-Meteo workflow (polygon file)
    ./workflow_generator.py --polygons-file polygons.json \
        --polygon-ids field1 field2 --output workflow.yml

    # Edge-to-cloud with DPU
    ./workflow_generator.py --polygon-ids abc123 --enable-dpu \
        --edge-site edgepool --cloud-site cloudpool --output workflow.yml
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from Pegasus.api import *

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SoilMoistureWorkflow:
    """Generate Pegasus workflow for soil moisture analysis and irrigation prediction."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "soilmoisture"

    def __init__(self, dagfile="workflow.yml"):
        """Initialize workflow."""
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

    def create_pegasus_properties(self):
        """Create Pegasus properties configuration."""
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"

    def create_sites_catalog(self, exec_site_name="condorpool"):
        """Create site catalog."""
        logger.info(f"Creating site catalog for execution site: {exec_site_name}")
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

    def create_replica_catalog(self):
        """Create replica catalog for input files."""
        logger.info("Creating replica catalog")
        self.rc = ReplicaCatalog()

    def create_transformation_catalog(self, exec_site_name="condorpool", container_image="kthare10/soilmoisture:latest"):
        """Create transformation catalog with executables and containers."""
        logger.info("Creating transformation catalog")
        self.tc = TransformationCatalog()

        # Container - use Singularity with docker:// URL
        soilmoisture_container = Container(
            "soilmoisture_container",
            container_type=Container.SINGULARITY,
            image=f"docker://{container_image}",
            image_site="docker_hub",
        )

        # Add transformations
        fetch_soil_data = Transformation(
            "fetch_soil_data",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "fetch_soil_data.py"),
            is_stageable=True,
            container=soilmoisture_container,
        ).add_pegasus_profile(memory="1 GB")

        analyze_moisture = Transformation(
            "analyze_moisture",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/analyze_moisture.py"),
            is_stageable=True,
            container=soilmoisture_container,
        ).add_pegasus_profile(memory="1 GB")

        train_model = Transformation(
            "train_model",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/train_model.py"),
            is_stageable=True,
            container=soilmoisture_container,
        ).add_pegasus_profile(memory="4 GB")

        predict_irrigation = Transformation(
            "predict_irrigation",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/predict_irrigation.py"),
            is_stageable=True,
            container=soilmoisture_container,
        ).add_pegasus_profile(memory="2 GB")

        visualize_moisture = Transformation(
            "visualize_moisture",
            site=exec_site_name,
            pfn=os.path.join(self.wf_dir, "bin/visualize_moisture.py"),
            is_stageable=True,
            container=soilmoisture_container,
        ).add_pegasus_profile(memory="2 GB")

        self.tc.add_containers(soilmoisture_container)
        self.tc.add_transformations(
            fetch_soil_data,
            analyze_moisture,
            train_model,
            predict_irrigation,
            visualize_moisture,
        )

    def create_workflow(self, args):
        """Create the complete workflow with ML training."""
        logger.info("Creating workflow")
        self.wf = Workflow(self.wf_name)

        polygons_file = File(os.path.basename(args.polygons_file))
        self.rc.add_replica("local", polygons_file, os.path.abspath(args.polygons_file))

        # Shared ML model files
        model_file = File("soil_moisture_model.pt")
        model_metadata = File("soil_moisture_model_metadata.json")

        # Collect all fetch and analyze jobs
        fetch_jobs = []
        analyze_jobs = []
        soil_data_files = []

        # Add fetch and analyze jobs for each polygon
        for polygon_id in args.polygon_ids:
            fetch_job, analyze_job, soil_data_file = self._add_fetch_analyze_jobs(
                polygon_id, args, polygons_file
            )
            fetch_jobs.append(fetch_job)
            analyze_jobs.append(analyze_job)
            soil_data_files.append(soil_data_file)

        # Add ML training job (uses all soil data files)
        train_job = self._add_train_job(soil_data_files, model_file, model_metadata, args)

        # Training depends on having fetched data
        self.wf.add_dependency(fetch_jobs[0], children=[train_job])

        # Add predict and visualize jobs for each polygon (uses trained model)
        for i, polygon_id in enumerate(args.polygon_ids):
            self._add_predict_visualize_jobs(
                polygon_id, args,
                soil_data_files[i], analyze_jobs[i],
                model_file, model_metadata, train_job
            )

    def _add_fetch_analyze_jobs(self, polygon_id: str, args, polygons_file: File):
        """Add fetch and analyze jobs for a polygon. Returns jobs and data file."""
        soil_data_file = File(f"{polygon_id}_soil_data.csv")
        analysis_file = File(f"{polygon_id}_analysis.json")

        # Job 1: Fetch soil data
        fetch_job = Job("fetch_soil_data", _id=f"fetch_{polygon_id}", node_label=f"fetch_{polygon_id}")
        fetch_job.add_args("--fetch", "--polygon-id", polygon_id)
        fetch_job.add_args("--start-date", args.start_date, "--end-date", args.end_date)
        fetch_job.add_args("--output", soil_data_file)
        fetch_job.add_args("--polygons-file", polygons_file)
        fetch_job.add_inputs(polygons_file)
        fetch_job.add_outputs(soil_data_file, stage_out=True, register_replica=False)
        fetch_job.add_pegasus_profile(label=polygon_id)

        # Job 2: Analyze moisture
        analyze_job = Job("analyze_moisture", _id=f"analyze_{polygon_id}", node_label=f"analyze_{polygon_id}")
        analyze_job.add_args(
            "--input", soil_data_file,
            "--output", analysis_file,
            "--crop-type", args.crop_type,
            "--soil-type", args.soil_type,
        )
        analyze_job.add_inputs(soil_data_file)
        analyze_job.add_outputs(analysis_file, stage_out=True, register_replica=False)
        analyze_job.add_pegasus_profile(label=polygon_id)

        # Add jobs to workflow
        self.wf.add_jobs(fetch_job, analyze_job)
        self.wf.add_dependency(fetch_job, children=[analyze_job])

        return fetch_job, analyze_job, soil_data_file

    def _add_train_job(self, soil_data_files, model_file, model_metadata, args):
        """Add ML model training job."""
        # For training, we use the first polygon's data
        # In a more sophisticated version, we could merge all data
        primary_data_file = soil_data_files[0]

        train_job = Job("train_model", _id="train_ml_model", node_label="train_ml_model")
        train_job.add_args(
            "--input", primary_data_file,
            "--output", model_file,
            "--metadata", model_metadata,
            "--sequence-length", "24",
            "--forecast-horizon", "24",
            "--epochs", str(args.ml_epochs),
        )
        train_job.add_inputs(primary_data_file)
        train_job.add_outputs(model_file, stage_out=True, register_replica=False)
        train_job.add_outputs(model_metadata, stage_out=True, register_replica=False)
        train_job.add_pegasus_profile(label="ml_training")

        self.wf.add_jobs(train_job)

        return train_job

    def _add_predict_visualize_jobs(self, polygon_id: str, args,
                                     soil_data_file, analyze_job,
                                     model_file, model_metadata, train_job):
        """Add predict and visualize jobs for a polygon using trained ML model."""
        analysis_file = File(f"{polygon_id}_analysis.json")
        prediction_file = File(f"{polygon_id}_prediction.json")
        visualization_file = File(f"{polygon_id}_visualization.png")

        # Job 3: Predict irrigation (with ML model)
        predict_job = Job("predict_irrigation", _id=f"predict_{polygon_id}", node_label=f"predict_{polygon_id}")
        predict_job.add_args(
            "--analysis", analysis_file,
            "--output", prediction_file,
            "--model", model_file,
            "--model-metadata", model_metadata,
            "--soil-data", soil_data_file,
        )
        predict_job.add_inputs(analysis_file, model_file, model_metadata, soil_data_file)
        predict_job.add_outputs(prediction_file, stage_out=True, register_replica=False)
        predict_job.add_pegasus_profile(label=polygon_id)

        # Job 4: Visualize
        visualize_job = Job("visualize_moisture", _id=f"visualize_{polygon_id}", node_label=f"visualize_{polygon_id}")
        visualize_job.add_args(
            "--data", soil_data_file,
            "--analysis", analysis_file,
            "--prediction", prediction_file,
            "--output", visualization_file,
        )
        visualize_job.add_inputs(soil_data_file, analysis_file, prediction_file)
        visualize_job.add_outputs(visualization_file, stage_out=True, register_replica=False)
        visualize_job.add_pegasus_profile(label=polygon_id)

        # Add jobs to workflow
        self.wf.add_jobs(predict_job, visualize_job)

        # Dependencies
        self.wf.add_dependency(analyze_job, children=[predict_job])
        self.wf.add_dependency(train_job, children=[predict_job])
        self.wf.add_dependency(predict_job, children=[visualize_job])


def main():
    parser = argparse.ArgumentParser(
        description="Generate Pegasus workflow for soil moisture analysis"
    )

    # Required arguments
    parser.add_argument(
        "--polygon-ids", "-p",
        type=str,
        nargs="+",
        required=False,
        help="Polygon IDs to analyze (optional, uses all polygons if omitted)"
    )
    parser.add_argument(
        "--polygons-file",
        type=str,
        required=True,
        help="JSON file with polygon definitions"
    )

    # Date range
    parser.add_argument(
        "--start-date",
        type=str,
        default=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        help="Start date for data fetch (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=datetime.now().strftime("%Y-%m-%d"),
        help="End date for data fetch (YYYY-MM-DD)"
    )

    # Crop and soil parameters
    parser.add_argument(
        "--crop-type",
        type=str,
        default="default",
        choices=["tomato", "corn", "wheat", "lettuce", "potato", "grape", "alfalfa", "cotton", "default"],
        help="Crop type for threshold selection"
    )
    parser.add_argument(
        "--soil-type",
        type=str,
        default="loam",
        choices=["sand", "sandy_loam", "loam", "clay_loam", "clay", "default"],
        help="Soil type for water capacity calculation"
    )

    # ML training parameters
    parser.add_argument(
        "--ml-epochs",
        type=int,
        default=50,
        help="Number of epochs for ML model training"
    )

    # Execution site
    parser.add_argument(
        "-e", "--execution-site-name",
        type=str,
        default="condorpool",
        help="HTCondor pool name for execution"
    )

    # Container
    parser.add_argument(
        "--container-image",
        type=str,
        default="kthare10/soilmoisture:latest",
        help="Docker container image for workflow"
    )

    # Output
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="workflow.yml",
        help="Output workflow file"
    )

    args = parser.parse_args()

    # Validate dates
    try:
        datetime.strptime(args.start_date, "%Y-%m-%d")
        datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)

    # Generate workflow
    try:
        if not args.polygon_ids:
            with open(args.polygons_file, "r") as f:
                data = json.load(f)
            polygons = data.get("polygons", data) if isinstance(data, dict) else data
            args.polygon_ids = [p.get("id") for p in polygons if p.get("id")]
            if not args.polygon_ids:
                logger.error("No polygon IDs found in polygons file")
                sys.exit(1)
        workflow = SoilMoistureWorkflow(dagfile=args.output)
        workflow.create_pegasus_properties()
        workflow.create_sites_catalog(exec_site_name=args.execution_site_name)
        workflow.create_replica_catalog()
        workflow.create_transformation_catalog(
            exec_site_name=args.execution_site_name,
            container_image=args.container_image
        )
        workflow.create_workflow(args)
        workflow.write()

        logger.info("\n" + "=" * 70)
        logger.info("WORKFLOW GENERATION COMPLETE")
        logger.info("=" * 70)
        logger.info(f"  Workflow file: {args.output}")
        logger.info(f"  Polygons: {len(args.polygon_ids)}")
        logger.info(f"  Crop type: {args.crop_type}")
        logger.info(f"  Soil type: {args.soil_type}")
        logger.info(f"  ML training epochs: {args.ml_epochs}")
        logger.info(f"  ML enabled: Yes (LSTM soil moisture prediction)")
        logger.info("\nNext steps:")
        logger.info(f"  1. Review workflow: {args.output}")
        logger.info(f"  2. Submit workflow: pegasus-plan --submit -s {args.execution_site_name} -o local {args.output}")
        logger.info(f"  3. Monitor status:  pegasus-status <submit_dir>")
        logger.info("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"Failed to generate workflow: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
