#!/usr/bin/env python3

"""[CUSTOMIZE] Description of what this pipeline step does.

This wrapper script is invoked by Pegasus as a job. It:
1. Parses arguments matching what workflow_generator.py passes via add_args()
2. Creates output directories as needed
3. Runs the underlying tool via subprocess (or calls a Python library directly)
4. Propagates exit codes so Pegasus can detect failures

References:
  - tnseq-workflow/bin/clip.py — subprocess wrapper calling a Java tool
  - earthquake-workflow/bin/fetch_earthquake_data.py — API fetch wrapper
  - soilmoisture-workflow/bin/analyze_moisture.py — pure-Python analysis
"""

import argparse
import logging
import os
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="[CUSTOMIZE] Step description")

    # [CUSTOMIZE] Arguments must match what workflow_generator.py passes
    # in the Job's add_args() call.
    parser.add_argument("--input", required=True, help="Input file")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--threads", type=int, default=1, help="Thread count")

    # [CUSTOMIZE] For fan-in jobs that accept multiple files:
    # parser.add_argument("--input", action="append", required=True,
    #                     help="Input file (can be specified multiple times)")

    # [CUSTOMIZE] For API-fetching jobs:
    # parser.add_argument("--region", required=True, help="Region name")
    # parser.add_argument("--start-date", required=True, help="Start date")

    args = parser.parse_args()

    # Log inputs for debugging via pegasus-analyzer
    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output}")

    # Create output directory if needed (Pegasus job dirs are flat)
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ==================================================================
    # [CUSTOMIZE] Choose your execution pattern:
    # ==================================================================

    # --- Pattern A: subprocess call to an external tool ---
    # (See tnseq-workflow/bin/clip.py, bwa_mem.py)
    cmd = [
        "mytool",
        "--input", args.input,
        "--output", args.output,
        "--threads", str(args.threads),
    ]

    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    # --- Pattern B: shell command with pipes ---
    # (See tnseq-workflow/bin/genomecov.py)
    # cmd = f"tool1 {args.input} | tool2 > {args.output}"
    # result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    # --- Pattern C: API fetch (no subprocess) ---
    # (See earthquake-workflow/bin/fetch_earthquake_data.py)
    # import requests
    # response = requests.get(api_url, params=params, timeout=60)
    # response.raise_for_status()
    # with open(args.output, "w") as f:
    #     f.write(response.text)

    # --- Pattern D: Pure Python analysis ---
    # (See soilmoisture-workflow/bin/analyze_moisture.py)
    # import pandas as pd
    # df = pd.read_csv(args.input)
    # results = analyze(df)
    # results.to_json(args.output)

    # --- Pattern E: Support file in working directory ---
    # Pegasus stages support files (R scripts, JARs) into the job's cwd
    # when they are added to the Replica Catalog + job inputs.
    # (See tnseq-workflow/bin/clip.py — je_jar, tab_generate.py — tab.R)
    # script_path = os.path.join(os.getcwd(), "analysis.R")

    # Verify output was created
    if not os.path.exists(args.output):
        logger.error(f"Expected output not found: {args.output}")
        sys.exit(1)

    logger.info(f"Output: {args.output}")


if __name__ == "__main__":
    main()
