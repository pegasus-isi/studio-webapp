#!/usr/bin/env python3

"""
Pegasus workflow generator for GWAS Quality Control pipeline.

Implements a multi-step QC pipeline for genome-wide association studies (GWAS):
  1. MAP file validation (allele security check)
  2. Auto-map cleaning and ID regeneration
  3. Fork into two parallel branches:
     - Left: Genotype QC (call rates, missing rates, het outliers)
     - Right: Quality preprocessing and downstream investigation
  4. Merge at ancestry estimation (PCA projection)
  5. Self-identification evaluation
  6. Multiethnic intersection and PCA merging
  7. Twin/duplicate checking and interval sample processing
  8. Expert subset validation and overlap rules
  9. Config generation (gwas_config.json + analysis_config.json)
  10. Final aggregation of post-QC results

Usage:
    ./workflow_generator.py --bed input.bed --bim input.bim --fam input.fam \\
        --map input.map --ref-panel ref_panel.bed --output workflow.yml

    ./workflow_generator.py --bed input.bed --bim input.bim --fam input.fam \\
        --map input.map --ref-panel ref_panel.bed \\
        --call-rate-threshold 0.98 --het-sd-threshold 3.0 \\
        -e condorpool --output workflow.yml
"""

import argparse
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

# Per-tool resource configuration
TOOL_CONFIGS = {
    "map_file_security":       {"memory": "2 GB",  "cores": 1},
    "auto_map_clean":          {"memory": "4 GB",  "cores": 1},
    "plink_call_rates":        {"memory": "8 GB",  "cores": 2},
    "calculate_missing_rate":  {"memory": "8 GB",  "cores": 2},
    "qc_threshold_check":      {"memory": "4 GB",  "cores": 1},
    "aggregate_het_outliers":  {"memory": "8 GB",  "cores": 2},
    "auto_preprocess":         {"memory": "4 GB",  "cores": 2},
    "downstream_investigation":{"memory": "4 GB",  "cores": 1},
    "ancestry_estimation":     {"memory": "16 GB", "cores": 4},
    "evaluate_self_identify":  {"memory": "4 GB",  "cores": 1},
    "results_evaluation":      {"memory": "2 GB",  "cores": 1},
    "multiethnic_intersect":   {"memory": "4 GB",  "cores": 1},
    "add_pca_merge":           {"memory": "8 GB",  "cores": 2},
    "twins_check":             {"memory": "4 GB",  "cores": 1},
    "interval_samples":        {"memory": "4 GB",  "cores": 1},
    "subset_experts":          {"memory": "4 GB",  "cores": 1},
    "overlap_validation":      {"memory": "4 GB",  "cores": 1},
    "config_generation":       {"memory": "2 GB",  "cores": 1},
    "final_aggregation":       {"memory": "8 GB",  "cores": 2},
    "generate_visualizations": {"memory": "4 GB",  "cores": 1},
}


class GwasQcWorkflow:
    """Pegasus workflow for GWAS quality control pipeline."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "gwas_qc"

    # Input file paths (set from CLI args)
    bed_file = None
    bim_file = None
    fam_file = None
    map_file = None
    ref_panel_file = None

    # QC parameters
    call_rate_threshold = 0.95
    het_sd_threshold = 3.0

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
    def create_transformation_catalog(self, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()

        container = Container(
            "gwas_qc_container",
            container_type=Container.SINGULARITY,
            image="docker://kthare10/gwas-qc:latest",
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

        # Register PLINK genotype files
        if self.bed_file:
            self.rc.add_replica(
                "local", "input.bed",
                "file://" + os.path.abspath(self.bed_file)
            )
        if self.bim_file:
            self.rc.add_replica(
                "local", "input.bim",
                "file://" + os.path.abspath(self.bim_file)
            )
        if self.fam_file:
            self.rc.add_replica(
                "local", "input.fam",
                "file://" + os.path.abspath(self.fam_file)
            )

        # Register MAP file
        if self.map_file:
            self.rc.add_replica(
                "local", "input.map",
                "file://" + os.path.abspath(self.map_file)
            )

        # Register reference panel for ancestry estimation
        if self.ref_panel_file:
            self.rc.add_replica(
                "local", "ref_panel.bed",
                "file://" + os.path.abspath(self.ref_panel_file)
            )

    # ------------------------------------------------------------------
    # Workflow DAG
    # ------------------------------------------------------------------
    def create_workflow(self, args):
        """Create the GWAS QC workflow DAG."""
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        # ============================================================
        # Input File objects (registered in Replica Catalog)
        # ============================================================
        input_bed = File("input.bed")
        input_bim = File("input.bim")
        input_fam = File("input.fam")
        input_map = File("input.map")
        ref_panel = File("ref_panel.bed")

        # ============================================================
        # Step 1: MAP file security check
        # ============================================================
        validated_map = File("validated_map.map")
        map_security_report = File("map_security_report.json")

        map_security_job = (
            Job("map_file_security", _id="map_file_security",
                node_label="map_file_security")
            .add_args(
                "--map", input_map,
                "--bim", input_bim,
                "--output-map", validated_map,
                "--output-report", map_security_report,
            )
            .add_inputs(input_map, input_bim)
            .add_outputs(validated_map, stage_out=False, register_replica=False)
            .add_outputs(map_security_report, stage_out=True,
                         register_replica=False)
        )
        self.wf.add_jobs(map_security_job)

        # ============================================================
        # Step 2: Auto-map clean and ID regeneration
        # ============================================================
        cleaned_bed = File("cleaned.bed")
        cleaned_bim = File("cleaned.bim")
        cleaned_fam = File("cleaned.fam")
        id_mapping = File("id_mapping.csv")

        auto_map_clean_job = (
            Job("auto_map_clean", _id="auto_map_clean",
                node_label="auto_map_clean")
            .add_args(
                "--bed", input_bed,
                "--bim", input_bim,
                "--fam", input_fam,
                "--validated-map", validated_map,
                "--output-bed", cleaned_bed,
                "--output-bim", cleaned_bim,
                "--output-fam", cleaned_fam,
                "--output-id-mapping", id_mapping,
            )
            .add_inputs(input_bed, input_bim, input_fam, validated_map)
            .add_outputs(cleaned_bed, stage_out=False, register_replica=False)
            .add_outputs(cleaned_bim, stage_out=False, register_replica=False)
            .add_outputs(cleaned_fam, stage_out=False, register_replica=False)
            .add_outputs(id_mapping, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(auto_map_clean_job)

        # ============================================================
        # LEFT BRANCH: Genotype QC
        # ============================================================

        # Step 3: PLINK call rates
        sample_call_rates = File("sample_call_rates.txt")
        snp_call_rates = File("snp_call_rates.txt")

        plink_call_rates_job = (
            Job("plink_call_rates", _id="plink_call_rates",
                node_label="plink_call_rates")
            .add_args(
                "--bed", cleaned_bed,
                "--bim", cleaned_bim,
                "--fam", cleaned_fam,
                "--output-sample-rates", sample_call_rates,
                "--output-snp-rates", snp_call_rates,
            )
            .add_inputs(cleaned_bed, cleaned_bim, cleaned_fam)
            .add_outputs(sample_call_rates, stage_out=False,
                         register_replica=False)
            .add_outputs(snp_call_rates, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(plink_call_rates_job)

        # Step 4: Calculate missing rates
        missing_rate_report = File("missing_rate_report.json")
        filtered_snps = File("filtered_snps.txt")

        calc_missing_job = (
            Job("calculate_missing_rate", _id="calculate_missing_rate",
                node_label="calculate_missing_rate")
            .add_args(
                "--sample-rates", sample_call_rates,
                "--snp-rates", snp_call_rates,
                "--threshold", str(self.call_rate_threshold),
                "--output-report", missing_rate_report,
                "--output-filtered-snps", filtered_snps,
            )
            .add_inputs(sample_call_rates, snp_call_rates)
            .add_outputs(missing_rate_report, stage_out=True,
                         register_replica=False)
            .add_outputs(filtered_snps, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(calc_missing_job)

        # Step 5: QC threshold check
        qc_pass_samples = File("qc_pass_samples.txt")
        qc_fail_samples = File("qc_fail_samples.txt")
        qc_threshold_report = File("qc_threshold_report.json")

        qc_threshold_job = (
            Job("qc_threshold_check", _id="qc_threshold_check",
                node_label="qc_threshold_check")
            .add_args(
                "--missing-report", missing_rate_report,
                "--filtered-snps", filtered_snps,
                "--bed", cleaned_bed,
                "--bim", cleaned_bim,
                "--fam", cleaned_fam,
                "--threshold", str(self.call_rate_threshold),
                "--output-pass", qc_pass_samples,
                "--output-fail", qc_fail_samples,
                "--output-report", qc_threshold_report,
            )
            .add_inputs(missing_rate_report, filtered_snps,
                        cleaned_bed, cleaned_bim, cleaned_fam)
            .add_outputs(qc_pass_samples, stage_out=False,
                         register_replica=False)
            .add_outputs(qc_fail_samples, stage_out=True,
                         register_replica=False)
            .add_outputs(qc_threshold_report, stage_out=True,
                         register_replica=False)
        )
        self.wf.add_jobs(qc_threshold_job)

        # Step 6: Aggregate heterozygosity outliers
        het_outliers = File("het_outliers.txt")
        het_report = File("het_report.json")
        genotype_qc_bed = File("genotype_qc.bed")
        genotype_qc_bim = File("genotype_qc.bim")
        genotype_qc_fam = File("genotype_qc.fam")

        het_outliers_job = (
            Job("aggregate_het_outliers", _id="aggregate_het_outliers",
                node_label="aggregate_het_outliers")
            .add_args(
                "--bed", cleaned_bed,
                "--bim", cleaned_bim,
                "--fam", cleaned_fam,
                "--qc-pass-samples", qc_pass_samples,
                "--het-sd-threshold", str(self.het_sd_threshold),
                "--output-outliers", het_outliers,
                "--output-report", het_report,
                "--output-bed", genotype_qc_bed,
                "--output-bim", genotype_qc_bim,
                "--output-fam", genotype_qc_fam,
            )
            .add_inputs(cleaned_bed, cleaned_bim, cleaned_fam, qc_pass_samples)
            .add_outputs(het_outliers, stage_out=True, register_replica=False)
            .add_outputs(het_report, stage_out=True, register_replica=False)
            .add_outputs(genotype_qc_bed, stage_out=False,
                         register_replica=False)
            .add_outputs(genotype_qc_bim, stage_out=False,
                         register_replica=False)
            .add_outputs(genotype_qc_fam, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(het_outliers_job)

        # ============================================================
        # RIGHT BRANCH: Quality preprocessing
        # ============================================================

        # Step 7: Auto preprocessing
        preprocessed_bed = File("preprocessed.bed")
        preprocessed_bim = File("preprocessed.bim")
        preprocessed_fam = File("preprocessed.fam")
        preprocess_log = File("preprocess_log.json")

        auto_preprocess_job = (
            Job("auto_preprocess", _id="auto_preprocess",
                node_label="auto_preprocess")
            .add_args(
                "--bed", cleaned_bed,
                "--bim", cleaned_bim,
                "--fam", cleaned_fam,
                "--output-bed", preprocessed_bed,
                "--output-bim", preprocessed_bim,
                "--output-fam", preprocessed_fam,
                "--output-log", preprocess_log,
            )
            .add_inputs(cleaned_bed, cleaned_bim, cleaned_fam)
            .add_outputs(preprocessed_bed, stage_out=False,
                         register_replica=False)
            .add_outputs(preprocessed_bim, stage_out=False,
                         register_replica=False)
            .add_outputs(preprocessed_fam, stage_out=False,
                         register_replica=False)
            .add_outputs(preprocess_log, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(auto_preprocess_job)

        # Step 8: Downstream investigation
        investigation_report = File("investigation_report.json")
        quality_flags = File("quality_flags.csv")

        downstream_job = (
            Job("downstream_investigation",
                _id="downstream_investigation",
                node_label="downstream_investigation")
            .add_args(
                "--bed", preprocessed_bed,
                "--bim", preprocessed_bim,
                "--fam", preprocessed_fam,
                "--preprocess-log", preprocess_log,
                "--output-report", investigation_report,
                "--output-flags", quality_flags,
            )
            .add_inputs(preprocessed_bed, preprocessed_bim,
                        preprocessed_fam, preprocess_log)
            .add_outputs(investigation_report, stage_out=True,
                         register_replica=False)
            .add_outputs(quality_flags, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(downstream_job)

        # ============================================================
        # MERGE POINT: Ancestry estimation (consumes from both branches)
        # ============================================================

        # Step 9: Ancestry estimation / PCA projection
        ancestry_results = File("ancestry_results.json")
        pca_eigenvec = File("pca_eigenvec.txt")
        pca_eigenval = File("pca_eigenval.txt")

        ancestry_job = (
            Job("ancestry_estimation", _id="ancestry_estimation",
                node_label="ancestry_estimation")
            .add_args(
                "--genotype-bed", genotype_qc_bed,
                "--genotype-bim", genotype_qc_bim,
                "--genotype-fam", genotype_qc_fam,
                "--quality-flags", quality_flags,
                "--ref-panel", ref_panel,
                "--output-ancestry", ancestry_results,
                "--output-eigenvec", pca_eigenvec,
                "--output-eigenval", pca_eigenval,
            )
            .add_inputs(genotype_qc_bed, genotype_qc_bim, genotype_qc_fam,
                        quality_flags, ref_panel)
            .add_outputs(ancestry_results, stage_out=True,
                         register_replica=False)
            .add_outputs(pca_eigenvec, stage_out=False,
                         register_replica=False)
            .add_outputs(pca_eigenval, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(ancestry_job)

        # ============================================================
        # Post-merge linear pipeline
        # ============================================================

        # Step 10: Evaluate self-identification
        sir_report = File("sir_report.json")
        ancestry_vs_sir = File("ancestry_vs_sir.csv")

        eval_sir_job = (
            Job("evaluate_self_identify", _id="evaluate_self_identify",
                node_label="evaluate_self_identify")
            .add_args(
                "--ancestry-results", ancestry_results,
                "--fam", genotype_qc_fam,
                "--output-report", sir_report,
                "--output-comparison", ancestry_vs_sir,
            )
            .add_inputs(ancestry_results, genotype_qc_fam)
            .add_outputs(sir_report, stage_out=True, register_replica=False)
            .add_outputs(ancestry_vs_sir, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(eval_sir_job)

        # Step 11: Results evaluation
        evaluation_summary = File("evaluation_summary.json")
        plot_data = File("plot_data.csv")

        results_eval_job = (
            Job("results_evaluation", _id="results_evaluation",
                node_label="results_evaluation")
            .add_args(
                "--sir-report", sir_report,
                "--ancestry-vs-sir", ancestry_vs_sir,
                "--pca-eigenvec", pca_eigenvec,
                "--output-summary", evaluation_summary,
                "--output-plot-data", plot_data,
            )
            .add_inputs(sir_report, ancestry_vs_sir, pca_eigenvec)
            .add_outputs(evaluation_summary, stage_out=True,
                         register_replica=False)
            .add_outputs(plot_data, stage_out=False, register_replica=False)
        )
        self.wf.add_jobs(results_eval_job)

        # Step 12: Multiethnic intersection
        multiethnic_output = File("multiethnic_intersect.csv")

        multiethnic_job = (
            Job("multiethnic_intersect", _id="multiethnic_intersect",
                node_label="multiethnic_intersect")
            .add_args(
                "--ancestry-results", ancestry_results,
                "--evaluation-summary", evaluation_summary,
                "--output", multiethnic_output,
            )
            .add_inputs(ancestry_results, evaluation_summary)
            .add_outputs(multiethnic_output, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(multiethnic_job)

        # Step 13: Add PCA merge
        pca_merged = File("pca_merged.txt")
        pca_cases = File("pca_cases.txt")

        pca_merge_job = (
            Job("add_pca_merge", _id="add_pca_merge",
                node_label="add_pca_merge")
            .add_args(
                "--pca-eigenvec", pca_eigenvec,
                "--multiethnic", multiethnic_output,
                "--genotype-fam", genotype_qc_fam,
                "--output-merged", pca_merged,
                "--output-cases", pca_cases,
            )
            .add_inputs(pca_eigenvec, multiethnic_output, genotype_qc_fam)
            .add_outputs(pca_merged, stage_out=False, register_replica=False)
            .add_outputs(pca_cases, stage_out=False, register_replica=False)
        )
        self.wf.add_jobs(pca_merge_job)

        # Step 14: Twins / duplicate check
        twins_report = File("twins_report.json")
        deduplicated_samples = File("deduplicated_samples.txt")

        twins_job = (
            Job("twins_check", _id="twins_check",
                node_label="twins_check")
            .add_args(
                "--genotype-bed", genotype_qc_bed,
                "--genotype-bim", genotype_qc_bim,
                "--genotype-fam", genotype_qc_fam,
                "--pca-merged", pca_merged,
                "--output-report", twins_report,
                "--output-samples", deduplicated_samples,
            )
            .add_inputs(genotype_qc_bed, genotype_qc_bim, genotype_qc_fam,
                        pca_merged)
            .add_outputs(twins_report, stage_out=True, register_replica=False)
            .add_outputs(deduplicated_samples, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(twins_job)

        # Step 15: Interval samples
        interval_output = File("interval_samples.csv")

        interval_job = (
            Job("interval_samples", _id="interval_samples",
                node_label="interval_samples")
            .add_args(
                "--deduplicated-samples", deduplicated_samples,
                "--twins-report", twins_report,
                "--pca-cases", pca_cases,
                "--output", interval_output,
            )
            .add_inputs(deduplicated_samples, twins_report, pca_cases)
            .add_outputs(interval_output, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(interval_job)

        # Step 16: Subset experts
        expert_subset = File("expert_subset.csv")

        subset_job = (
            Job("subset_experts", _id="subset_experts",
                node_label="subset_experts")
            .add_args(
                "--interval-samples", interval_output,
                "--pca-merged", pca_merged,
                "--output", expert_subset,
            )
            .add_inputs(interval_output, pca_merged)
            .add_outputs(expert_subset, stage_out=False,
                         register_replica=False)
        )
        self.wf.add_jobs(subset_job)

        # Step 17: Overlap validation
        validated_results = File("validated_results.json")

        overlap_job = (
            Job("overlap_validation", _id="overlap_validation",
                node_label="overlap_validation")
            .add_args(
                "--expert-subset", expert_subset,
                "--multiethnic", multiethnic_output,
                "--output", validated_results,
            )
            .add_inputs(expert_subset, multiethnic_output)
            .add_outputs(validated_results, stage_out=True,
                         register_replica=False)
        )
        self.wf.add_jobs(overlap_job)

        # Step 18: Config generation (produces intermediate for fork)
        config_data = File("config_data.json")

        config_gen_job = (
            Job("config_generation", _id="config_generation",
                node_label="config_generation")
            .add_args(
                "--validated-results", validated_results,
                "--pca-merged", pca_merged,
                "--pca-cases", pca_cases,
                "--het-report", het_report,
                "--missing-report", missing_rate_report,
                "--output", config_data,
            )
            .add_inputs(validated_results, pca_merged, pca_cases,
                        het_report, missing_rate_report)
            .add_outputs(config_data, stage_out=False, register_replica=False)
        )
        self.wf.add_jobs(config_gen_job)

        # ============================================================
        # SECOND FORK: Two parallel config output jobs
        # ============================================================

        # Step 19a: GWAS config
        gwas_config = File("gwas_config.json")

        gwas_config_job = (
            Job("config_generation", _id="gwas_config",
                node_label="gwas_config")
            .add_args(
                "--validated-results", validated_results,
                "--pca-merged", pca_merged,
                "--pca-cases", pca_cases,
                "--het-report", het_report,
                "--missing-report", missing_rate_report,
                "--output", config_data,
                "--mode", "gwas",
                "--config-input", config_data,
                "--config-output", gwas_config,
            )
            .add_inputs(config_data)
            .add_outputs(gwas_config, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(gwas_config_job)

        # Step 19b: Analysis config
        analysis_config = File("analysis_config.json")

        analysis_config_job = (
            Job("config_generation", _id="analysis_config",
                node_label="analysis_config")
            .add_args(
                "--validated-results", validated_results,
                "--pca-merged", pca_merged,
                "--pca-cases", pca_cases,
                "--het-report", het_report,
                "--missing-report", missing_rate_report,
                "--output", config_data,
                "--mode", "analysis",
                "--config-input", config_data,
                "--config-output", analysis_config,
            )
            .add_inputs(config_data)
            .add_outputs(analysis_config, stage_out=True,
                         register_replica=False)
        )
        self.wf.add_jobs(analysis_config_job)

        # ============================================================
        # MERGE: Final aggregation
        # ============================================================

        # Step 20: Final aggregation
        postqc_bed = File("postqc.bed")
        postqc_bim = File("postqc.bim")
        postqc_fam = File("postqc.fam")
        qc_summary = File("qc_summary_report.json")

        final_job = (
            Job("final_aggregation", _id="final_aggregation",
                node_label="final_aggregation")
            .add_args(
                "--genotype-bed", genotype_qc_bed,
                "--genotype-bim", genotype_qc_bim,
                "--genotype-fam", genotype_qc_fam,
                "--gwas-config", gwas_config,
                "--analysis-config", analysis_config,
                "--validated-results", validated_results,
                "--ancestry-results", ancestry_results,
                "--het-report", het_report,
                "--missing-report", missing_rate_report,
                "--output-bed", postqc_bed,
                "--output-bim", postqc_bim,
                "--output-fam", postqc_fam,
                "--output-summary", qc_summary,
            )
            .add_inputs(genotype_qc_bed, genotype_qc_bim, genotype_qc_fam,
                        gwas_config, analysis_config, validated_results,
                        ancestry_results, het_report, missing_rate_report)
            .add_outputs(postqc_bed, stage_out=True, register_replica=False)
            .add_outputs(postqc_bim, stage_out=True, register_replica=False)
            .add_outputs(postqc_fam, stage_out=True, register_replica=False)
            .add_outputs(qc_summary, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(final_job)

        # ============================================================
        # Step 21: Generate QC visualizations
        # ============================================================
        pca_plot = File("pca_scatter.png")
        attrition_plot = File("sample_attrition.png")
        het_plot = File("heterozygosity_distribution.png")
        missing_plot = File("missing_rate_summary.png")
        validation_plot = File("variant_validation.png")

        viz_job = (
            Job("generate_visualizations", _id="generate_visualizations",
                node_label="generate_visualizations")
            .add_args(
                "--qc-summary", qc_summary,
                "--ancestry-results", ancestry_results,
                "--het-report", het_report,
                "--missing-report", missing_rate_report,
                "--map-security-report", map_security_report,
                "--twins-report", twins_report,
                "--output-pca", pca_plot,
                "--output-attrition", attrition_plot,
                "--output-het", het_plot,
                "--output-missing", missing_plot,
                "--output-validation", validation_plot,
            )
            .add_inputs(qc_summary, ancestry_results, het_report,
                        missing_rate_report, map_security_report,
                        twins_report)
            .add_outputs(pca_plot, stage_out=True, register_replica=False)
            .add_outputs(attrition_plot, stage_out=True,
                         register_replica=False)
            .add_outputs(het_plot, stage_out=True, register_replica=False)
            .add_outputs(missing_plot, stage_out=True,
                         register_replica=False)
            .add_outputs(validation_plot, stage_out=True,
                         register_replica=False)
        )
        self.wf.add_jobs(viz_job)


# ======================================================================
# main() — CLI argument parsing
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="GWAS Quality Control Pegasus Workflow Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --bed data/input.bed --bim data/input.bim --fam data/input.fam \\
           --map data/input.map --ref-panel references/ref_panel.bed

  %(prog)s --bed data/input.bed --bim data/input.bim --fam data/input.fam \\
           --map data/input.map --ref-panel references/ref_panel.bed \\
           --call-rate-threshold 0.98 --het-sd-threshold 3.0
""",
    )

    # Standard Pegasus arguments
    parser.add_argument(
        "-s", "--skip-sites-catalog", action="store_true",
        help="Skip site catalog creation",
    )
    parser.add_argument(
        "-e", "--execution-site-name", metavar="STR", type=str,
        default="condorpool",
        help="Execution site name (default: condorpool)",
    )
    parser.add_argument(
        "-o", "--output", metavar="STR", type=str, default="workflow.yml",
        help="Output file (default: workflow.yml)",
    )

    # GWAS-specific arguments
    parser.add_argument(
        "--bed", required=True, help="PLINK .bed genotype file",
    )
    parser.add_argument(
        "--bim", required=True, help="PLINK .bim variant file",
    )
    parser.add_argument(
        "--fam", required=True, help="PLINK .fam sample file",
    )
    parser.add_argument(
        "--map", required=True, help="PLINK .map file",
    )
    parser.add_argument(
        "--ref-panel", required=True,
        help="Reference panel file for ancestry estimation",
    )
    parser.add_argument(
        "--call-rate-threshold", type=float, default=0.95,
        help="Minimum call rate threshold (default: 0.95)",
    )
    parser.add_argument(
        "--het-sd-threshold", type=float, default=3.0,
        help="Heterozygosity SD threshold for outliers (default: 3.0)",
    )

    args = parser.parse_args()

    # Input validation
    for path_arg, label in [
        (args.bed, "BED"), (args.bim, "BIM"), (args.fam, "FAM"),
        (args.map, "MAP"), (args.ref_panel, "Reference panel"),
    ]:
        if not os.path.exists(path_arg):
            print(f"Error: {label} file not found: {path_arg}")
            sys.exit(1)

    logger.info("=" * 70)
    logger.info("GWAS QC WORKFLOW GENERATOR")
    logger.info("=" * 70)
    logger.info(f"BED:                {args.bed}")
    logger.info(f"BIM:                {args.bim}")
    logger.info(f"FAM:                {args.fam}")
    logger.info(f"MAP:                {args.map}")
    logger.info(f"Ref panel:          {args.ref_panel}")
    logger.info(f"Call rate threshold: {args.call_rate_threshold}")
    logger.info(f"Het SD threshold:   {args.het_sd_threshold}")
    logger.info(f"Execution site:     {args.execution_site_name}")
    logger.info(f"Output file:        {args.output}")
    logger.info("=" * 70)

    try:
        workflow = GwasQcWorkflow(dagfile=args.output)
        workflow.bed_file = args.bed
        workflow.bim_file = args.bim
        workflow.fam_file = args.fam
        workflow.map_file = args.map
        workflow.ref_panel_file = args.ref_panel
        workflow.call_rate_threshold = args.call_rate_threshold
        workflow.het_sd_threshold = args.het_sd_threshold

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
