"""
BigQuery tools for the Gold Layer Agent.
Handles schema inspection, table listing, and data sampling from Silver layer.
"""

import json
import logging
from typing import Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError

logger = logging.getLogger(__name__)


def validate_bigquery_access(project_id: str, dataset_id: str) -> dict:
    """
    Validate that the agent can access the specified BigQuery project and dataset.

    Args:
        project_id: GCP project ID.
        dataset_id: BigQuery dataset ID (Silver layer).

    Returns:
        dict with keys: success (bool), message (str), dataset_info (dict|None)
    """
    try:
        client = bigquery.Client(project=project_id)
        dataset_ref = client.dataset(dataset_id, project=project_id)
        dataset = client.get_dataset(dataset_ref)
        return {
            "success": True,
            "message": f"Successfully accessed dataset '{project_id}.{dataset_id}'.",
            "dataset_info": {
                "dataset_id": dataset.dataset_id,
                "project": dataset.project,
                "location": dataset.location,
                "description": dataset.description or "",
                "labels": dict(dataset.labels) if dataset.labels else {},
                "created": str(dataset.created) if dataset.created else None,
            },
        }
    except GoogleAPIError as e:
        logger.error("BigQuery access error: %s", e)
        return {"success": False, "message": str(e), "dataset_info": None}
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return {"success": False, "message": str(e), "dataset_info": None}


def list_silver_tables(project_id: str, dataset_id: str) -> dict:
    """
    List all tables and views in the Silver layer dataset.

    Args:
        project_id: GCP project ID.
        dataset_id: BigQuery Silver layer dataset ID.

    Returns:
        dict with keys: success (bool), tables (list[dict]), message (str)
    """
    try:
        client = bigquery.Client(project=project_id)
        tables = list(client.list_tables(f"{project_id}.{dataset_id}"))
        table_list = []
        for t in tables:
            table_list.append(
                {
                    "table_id": t.table_id,
                    "full_table_id": f"{project_id}.{dataset_id}.{t.table_id}",
                    "table_type": t.table_type,
                }
            )
        return {
            "success": True,
            "tables": table_list,
            "count": len(table_list),
            "message": f"Found {len(table_list)} objects in {project_id}.{dataset_id}.",
        }
    except GoogleAPIError as e:
        logger.error("Error listing tables: %s", e)
        return {"success": False, "tables": [], "count": 0, "message": str(e)}


def analyze_silver_schema(
    project_id: str, dataset_id: str, table_id: str
) -> dict:
    """
    Retrieve the full schema and metadata for a specific Silver layer table.

    Args:
        project_id: GCP project ID.
        dataset_id: BigQuery Silver layer dataset ID.
        table_id: Table name to analyze.

    Returns:
        dict with schema fields, row count, partitioning, clustering, and description.
    """
    try:
        client = bigquery.Client(project=project_id)
        table_ref = client.get_table(f"{project_id}.{dataset_id}.{table_id}")

        fields = []
        for field in table_ref.schema:
            fields.append(
                {
                    "name": field.name,
                    "field_type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or "",
                    "is_nullable": field.mode == "NULLABLE",
                }
            )

        # Partition info
        partition_info = None
        if table_ref.time_partitioning:
            partition_info = {
                "type": table_ref.time_partitioning.type_,
                "field": table_ref.time_partitioning.field,
                "expiration_ms": table_ref.time_partitioning.expiration_ms,
            }
        elif table_ref.range_partitioning:
            partition_info = {
                "type": "RANGE",
                "field": table_ref.range_partitioning.field,
            }

        clustering_fields = list(table_ref.clustering_fields or [])

        return {
            "success": True,
            "table_id": table_id,
            "full_table_id": f"{project_id}.{dataset_id}.{table_id}",
            "num_rows": table_ref.num_rows,
            "num_bytes": table_ref.num_bytes,
            "description": table_ref.description or "",
            "labels": dict(table_ref.labels) if table_ref.labels else {},
            "schema": fields,
            "column_count": len(fields),
            "partitioning": partition_info,
            "clustering": clustering_fields,
            "created": str(table_ref.created) if table_ref.created else None,
            "modified": str(table_ref.modified) if table_ref.modified else None,
        }
    except GoogleAPIError as e:
        logger.error("Schema analysis error: %s", e)
        return {"success": False, "table_id": table_id, "message": str(e)}


def sample_silver_data(
    project_id: str,
    dataset_id: str,
    table_id: str,
    sample_rows: int = 5,
) -> dict:
    """
    Sample a small number of rows from a Silver layer table for pattern inference.

    Args:
        project_id: GCP project ID.
        dataset_id: BigQuery Silver layer dataset ID.
        table_id: Table to sample.
        sample_rows: Number of rows to return (default 5, max 20).

    Returns:
        dict with sample rows as list of dicts and column stats.
    """
    sample_rows = min(sample_rows, 20)
    try:
        client = bigquery.Client(project=project_id)
        query = f"""
            SELECT *
            FROM `{project_id}.{dataset_id}.{table_id}`
            LIMIT {sample_rows}
        """
        result = client.query(query).result()
        rows = []
        for row in result:
            rows.append(dict(row))

        # Convert non-serialisable types to strings
        serialisable_rows = []
        for row in rows:
            clean = {}
            for k, v in row.items():
                try:
                    json.dumps(v)
                    clean[k] = v
                except (TypeError, ValueError):
                    clean[k] = str(v)
            serialisable_rows.append(clean)

        return {
            "success": True,
            "table_id": table_id,
            "sampled_rows": len(serialisable_rows),
            "data": serialisable_rows,
        }
    except GoogleAPIError as e:
        logger.error("Data sampling error: %s", e)
        return {
            "success": False,
            "table_id": table_id,
            "sampled_rows": 0,
            "data": [],
            "message": str(e),
        }
