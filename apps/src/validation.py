"""User-input validation used by the UI before any GCP calls are made."""

from __future__ import annotations

import re

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9\-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9\-_.]{1,61}[a-z0-9]$")


def validate_inputs(
    project: str,
    silver_dataset: str,
    gold_dataset: str,
    bucket: str,
) -> list[str]:
    """Return a list of human-readable error messages (empty if all valid)."""
    errors: list[str] = []

    project = (project or "").strip()
    silver_dataset = (silver_dataset or "").strip()
    gold_dataset = (gold_dataset or "").strip()
    bucket = (bucket or "").strip()

    if not project:
        errors.append("Source GCP project ID is required.")
    elif not _PROJECT_RE.match(project):
        errors.append(
            "GCP project ID looks invalid (lowercase letters, digits, hyphens; 6-30 chars)."
        )

    if not silver_dataset:
        errors.append("Source Silver dataset name is required.")
    elif not _DATASET_RE.match(silver_dataset):
        errors.append("Silver dataset name may contain only letters, digits and underscores.")

    if not gold_dataset:
        errors.append("Target Gold dataset name is required.")
    elif not _DATASET_RE.match(gold_dataset):
        errors.append("Gold dataset name may contain only letters, digits and underscores.")

    if silver_dataset and gold_dataset and silver_dataset == gold_dataset:
        errors.append("Gold dataset must be different from the Silver dataset.")

    if not bucket:
        errors.append("GCS bucket name is required.")
    elif not _BUCKET_RE.match(bucket):
        errors.append("GCS bucket name is not a valid bucket name.")

    return errors
