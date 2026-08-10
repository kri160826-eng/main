"""Thin, well-behaved wrapper around the BigQuery client.

Responsibilities:
  * connect using ADC / GOOGLE_APPLICATION_CREDENTIALS
  * inspect a Silver dataset (tables, schemas, row counts, samples, constraints)
  * create datasets / tables and run DDL / DML statements (used only after
    the user approves in the UI)

All identifiers are validated to avoid SQL-injection style mistakes when we
interpolate them into INFORMATION_SCHEMA queries.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def validate_identifier(value: str, kind: str) -> str:
    """Validate a project / dataset / table identifier."""
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{kind} must not be empty.")
    if not _IDENT_RE.match(value):
        raise ValueError(
            f"{kind} '{value}' is invalid. Use only letters, digits, hyphens and underscores."
        )
    return value


class BigQueryClient:
    def __init__(self, project: str, location: str = "US") -> None:
        self.project = validate_identifier(project, "Project id")
        self.location = location
        self._client = bigquery.Client(project=self.project, location=location)
        logger.info("Connected to BigQuery project '%s' (location=%s)", self.project, location)

    # -- Inspection --------------------------------------------------------

    def dataset_exists(self, dataset: str) -> bool:
        dataset = validate_identifier(dataset, "Dataset")
        try:
            self._client.get_dataset(f"{self.project}.{dataset}")
            return True
        except NotFound:
            return False

    def list_tables(self, dataset: str, limit: int | None = None) -> list[str]:
        dataset = validate_identifier(dataset, "Dataset")
        tables = [t.table_id for t in self._client.list_tables(f"{self.project}.{dataset}")]
        if limit:
            tables = tables[:limit]
        return tables

    def get_table_schema(self, dataset: str, table: str) -> list[dict[str, Any]]:
        dataset = validate_identifier(dataset, "Dataset")
        table = validate_identifier(table, "Table")
        tbl = self._client.get_table(f"{self.project}.{dataset}.{table}")
        return [
            {
                "name": f.name,
                "type": f.field_type,
                "mode": f.mode,
                "description": f.description,
            }
            for f in tbl.schema
        ]

    def get_row_counts(self, dataset: str) -> dict[str, int]:
        """Fast row counts via the __TABLES__ metadata table."""
        dataset = validate_identifier(dataset, "Dataset")
        query = f"""
            SELECT table_id, row_count
            FROM `{self.project}.{dataset}.__TABLES__`
        """
        try:
            rows = self._client.query(query, location=self.location).result()
            return {r["table_id"]: int(r["row_count"]) for r in rows}
        except GoogleAPICallError as exc:
            logger.warning("Could not read row counts for %s: %s", dataset, exc)
            return {}

    def get_sample_rows(self, dataset: str, table: str, limit: int = 20) -> list[dict[str, Any]]:
        dataset = validate_identifier(dataset, "Dataset")
        table = validate_identifier(table, "Table")
        query = f"SELECT * FROM `{self.project}.{dataset}.{table}` LIMIT {int(limit)}"
        try:
            df = self._client.query(query, location=self.location).result().to_dataframe(
                create_bqstorage_client=False
            )
            # Stringify everything so the sample is trivially JSON-serialisable.
            return df.astype(str).to_dict(orient="records")
        except Exception as exc:  # sampling is best-effort
            logger.warning("Could not sample %s.%s: %s", dataset, table, exc)
            return []

    def get_constraints(self, dataset: str) -> dict[str, dict[str, Any]]:
        """Read declared PK/FK constraints from INFORMATION_SCHEMA, if any.

        Returns {table_name: {"primary_key": [...], "foreign_keys": [...]}}.
        Many Silver datasets have no declared constraints; that is expected
        and simply yields an empty dict.
        """
        dataset = validate_identifier(dataset, "Dataset")
        result: dict[str, dict[str, Any]] = {}
        query = f"""
            SELECT
              tc.table_name,
              tc.constraint_type,
              kcu.column_name,
              kcu.ordinal_position
            FROM `{self.project}.{dataset}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` tc
            JOIN `{self.project}.{dataset}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE` kcu
              ON tc.constraint_name = kcu.constraint_name
            ORDER BY tc.table_name, kcu.ordinal_position
        """
        try:
            rows = self._client.query(query, location=self.location).result()
            for r in rows:
                entry = result.setdefault(
                    r["table_name"], {"primary_key": [], "foreign_keys": []}
                )
                if r["constraint_type"] == "PRIMARY KEY":
                    entry["primary_key"].append(r["column_name"])
                elif r["constraint_type"] == "FOREIGN KEY":
                    entry["foreign_keys"].append(r["column_name"])
        except GoogleAPICallError as exc:
            # INFORMATION_SCHEMA for constraints may be unavailable; not fatal.
            logger.info("No constraint metadata available for %s: %s", dataset, exc)
        return result

    # -- Execution (only after approval) -----------------------------------

    def create_dataset(self, dataset: str) -> bool:
        """Create the dataset if it does not exist. Returns True if created."""
        dataset = validate_identifier(dataset, "Dataset")
        ref = bigquery.Dataset(f"{self.project}.{dataset}")
        ref.location = self.location
        try:
            self._client.get_dataset(ref)
            logger.info("Dataset '%s' already exists; reusing it.", dataset)
            return False
        except NotFound:
            self._client.create_dataset(ref)
            logger.info("Created dataset '%s' in %s.", dataset, self.location)
            return True

    def run_statement(self, sql: str) -> int:
        """Run a single DDL/DML statement, returning affected/produced rows."""
        job = self._client.query(sql, location=self.location)
        job.result()  # wait for completion / raise on error
        return job.num_dml_affected_rows or 0
