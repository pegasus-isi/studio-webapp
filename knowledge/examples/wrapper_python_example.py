#!/usr/bin/env python3

"""Clip UMIs from FASTQ reads using Je."""

import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Clip UMIs using Je")
    parser.add_argument("--input", required=True, help="Input FASTQ file (.fq.gz)")
    parser.add_argument("--output", required=True, help="Output clipped FASTQ file")
    parser.add_argument("--je-jar", required=True, help="Path to je_1.2_bundle.jar")
    parser.add_argument("--umi-len", type=int, default=6, help="UMI length (default: 6)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    output_dir = os.path.dirname(args.output)
    cmd = [
        "java", "-Xmx5500m", "-jar", args.je_jar,
        "clip",
        f"F1={args.input}",
        f"LEN={args.umi_len}",
        f"O={output_dir}",
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    # Je outputs with _clipped suffix in the output directory; verify
    expected = args.output
    if not os.path.exists(expected):
        # Je names output based on input basename
        basename = os.path.basename(args.input).replace(".fq.gz", "_clipped.fq.gz")
        je_output = os.path.join(output_dir, basename)
        if os.path.exists(je_output) and je_output != expected:
            os.rename(je_output, expected)

    if not os.path.exists(expected):
        print(f"Error: Expected output not found: {expected}", file=sys.stderr)
        sys.exit(1)

    print(f"Output: {expected}")


if __name__ == "__main__":
    main()
