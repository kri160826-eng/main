"""Inspect a Silver dataset and produce a structured metadata document.

The output of `analyze()` is a plain dict that is both shown in the UI and
fed to the modeler. It intentionally contains everything a data modeler
would want to see: schemas, row counts, samples and any declared constraints.
"""

from __future__ import annotations

import logging
from typing import Any

from .bigquery_client import BigQueryClient

logger = logging.getLogger(__name__)


def analyze(
    client: BigQueryClient,
    dataset: str,
    selected_tables: list[str] | None = None,
    sample_row_limit: int = 20,
    max_tables: int = 100,
) -> dict[str, Any]:
    """Inspect the Silver dataset and return a metadata document."""
    logger.info("Analyzing Silver dataset '%s'...", dataset)

    all_tables = client.list_tables(dataset, limit=max_tables)
    if selected_tables:
        wanted = set(selected_tables)
        tables = [t for t in all_tables if t in wanted]
        missing = wanted - set(all_tables)
        if missing:
            logger.warning("Requested tables not found and skipped: %s", sorted(missing))
    else:
        tables = all_tables

    if not tables:
        raise ValueError(f"No tables found to analyze in dataset '{dataset}'.")

    row_counts = client.get_row_counts(dataset)
    constraints = client.get_constraints(dataset)

    table_docs: list[dict[str, Any]] = []
    for name in tables:
        logger.info("Inspecting table '%s'", name)
        schema = client.get_table_schema(dataset, name)
        sample = client.get_sample_rows(dataset, name, limit=sample_row_limit)
        table_docs.append(
            {
                "name": name,
                "row_count": row_counts.get(name),
                "columns": schema,
                "constraints": constraints.get(name, {}),
                "sample_rows": sample,
            }
        )

    metadata = {
        "project": client.project,
        "dataset": dataset,
        "table_count": len(table_docs),
        "tables": table_docs,
    }
    logger.info("Analysis complete: %d tables inspected.", len(table_docs))
    return metadata


def summarize_for_prompt(metadata: dict[str, Any], max_sample_rows: int = 5) -> dict[str, Any]:
    """Trim the metadata to a compact form suitable for an LLM prompt.

    Keeps schemas and constraints in full but limits sample rows to keep the
    token count manageable on wide/large datasets.
    """
    tables = []
    for t in metadata.get("tables", []):
        tables.append(
            {
                "name": t["name"],
                "row_count": t.get("row_count"),
                "columns": t["columns"],
                "constraints": t.get("constraints", {}),
                "sample_rows": (t.get("sample_rows") or [])[:max_sample_rows],
            }
        )
    return {
        "project": metadata.get("project"),
        "dataset": metadata.get("dataset"),
        "table_count": metadata.get("table_count"),
        "tables": tables,
    }
