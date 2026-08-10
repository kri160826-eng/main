"""Approval-gated execution: create the Gold dataset, tables, and load data.

Nothing in this module runs until the UI calls it, which only happens after
the user clicks "Approve and Create Gold Dataset". Each statement is executed
and logged individually so a partial failure is clearly reported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import ddl_generator, transform_generator
from .bigquery_client import BigQueryClient
from .models import Proposal

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class ExecutionResult:
    dataset_created: bool = False
    steps: list[StepResult] = field(default_factory=list)
    success: bool = True

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.steps.append(StepResult(label, ok, detail))
        if not ok:
            self.success = False


def execute(client: BigQueryClient, proposal: Proposal) -> ExecutionResult:
    """Create the Gold dataset, all tables, then run transformations."""
    result = ExecutionResult()
    project = client.project

    # 1) Dataset
    try:
        created = client.create_dataset(proposal.gold_dataset)
        result.dataset_created = created
        result.add(
            f"Create dataset '{proposal.gold_dataset}'",
            True,
            "created" if created else "already existed (reused)",
        )
    except Exception as exc:
        logger.exception("Failed to create dataset")
        result.add(f"Create dataset '{proposal.gold_dataset}'", False, str(exc))
        return result  # cannot continue without a dataset

    # 2) Tables (DDL) — dimensions before facts (handled by generator order)
    _, ddl_statements = ddl_generator.generate_ddl(project, proposal)
    ordered_tables = [*proposal.dimensions, *proposal.facts]
    for table, stmt in zip(ordered_tables, ddl_statements):
        try:
            client.run_statement(stmt)
            result.add(f"Create table '{table.name}'", True)
        except Exception as exc:
            logger.exception("Failed to create table %s", table.name)
            result.add(f"Create table '{table.name}'", False, str(exc))

    # 3) Transformations (load data) — only if all tables were created
    tables_ok = all(
        s.ok for s in result.steps if s.label.startswith("Create table")
    )
    if not tables_ok:
        result.add("Load data", False, "Skipped because table creation failed.")
        return result

    _, transform_statements = transform_generator.generate_transforms(project, proposal)
    for table, stmt in zip(ordered_tables, transform_statements):
        try:
            rows = client.run_statement(stmt)
            result.add(f"Load '{table.name}'", True, f"{rows} rows affected")
        except Exception as exc:
            logger.exception("Failed to load table %s", table.name)
            result.add(f"Load '{table.name}'", False, str(exc))

    return result
