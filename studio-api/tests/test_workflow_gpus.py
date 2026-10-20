"""services/workflow_gpus: the steps a workflow says need a GPU (#576)."""

from __future__ import annotations

from services.workflow_gpus import gpu_steps

WORKFLOW = """
pegasus: "5.0"
name: demo
jobs:
  - type: job
    name: fetch
    id: ID0000001
  - type: job
    name: train
    id: ID0000002
    profiles:
      pegasus: {gpus: 1}
transformationCatalog:
  transformations:
    - name: infer
      sites:
        - name: condorpool
          profiles:
            condor: {request_gpus: "2"}
    - name: plot
      profiles:
        condor: {request_gpus: 0}
"""


def test_finds_gpu_steps_on_jobs_and_transformation_sites(tmp_path):
    wf = tmp_path / "workflow.yml"
    wf.write_text(WORKFLOW)
    assert gpu_steps(str(tmp_path), str(wf)) == ["infer", "train"]


def test_reads_a_separate_transformation_catalog(tmp_path):
    (tmp_path / "transformations.yml").write_text(
        "transformations:\n  - name: gpu_tool\n    profiles:\n      pegasus: {gpus: 4}\n"
    )
    assert gpu_steps(str(tmp_path), None) == ["gpu_tool"]


def test_nothing_to_read_means_none_declared(tmp_path):
    assert gpu_steps(str(tmp_path), None) == []
    (tmp_path / "workflow.yml").write_text("{ not yaml")
    assert gpu_steps(str(tmp_path), str(tmp_path / "workflow.yml")) == []
