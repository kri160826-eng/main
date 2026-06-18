"""
GCS tools for the Gold Layer Agent.
"""

import json
import logging
from datetime import datetime, timezone

from google.cloud import storage
from google.api_core.exceptions import GoogleAPIError

logger = logging.getLogger(__name__)

# In-process cache: keyed by design_id returned from generate_gold_design()
_DESIGN_CACHE: dict = {}


def store_design(design_id: str, design: dict) -> None:
    """Called internally by generate_gold_design to cache its output."""
    _DESIGN_CACHE[design_id] = design


def create_gcs_config(
    bucket_name: str,
    project_id: str,
    gold_dataset_id: str,
    design_id: str,
) -> dict:
    """
    Write Gold layer metadata, SQL transformations, and DQ validations to GCS.

    Call this after generate_gold_design(). Pass the design_id value that
    generate_gold_design() returned — do NOT pass the full JSON blob.

    Args:
        bucket_name: GCS bucket name.
        project_id: GCP project ID.
        gold_dataset_id: Gold layer dataset name.
        design_id: The design_id string returned by generate_gold_design().

    Returns:
        dict with success flag and list of uploaded GCS URIs.
    """
    design = _DESIGN_CACHE.get(design_id)
    if not design:
        return {
            "success": False,
            "uploaded_files": [],
            "message": (
                f"No design found for design_id='{design_id}'. "
                "Call generate_gold_design() first and use the returned design_id."
            ),
        }

    metadata_config = design.get("metadata_config", {})
    sql_transformations = design.get("sql_transformations", {})
    dq_validations = design.get("dq_validations", {})

    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        uploaded = []
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        base_prefix = f"gold_layer_config/{gold_dataset_id}/{ts}"

        # ── Metadata JSON ─────────────────────────────────────────────────────
        meta_blob_path = f"{base_prefix}/metadata/gold_layer_metadata.json"
        blob = bucket.blob(meta_blob_path)
        blob.upload_from_string(
            json.dumps(metadata_config, indent=2, default=str),
            content_type="application/json",
        )
        uploaded.append(f"gs://{bucket_name}/{meta_blob_path}")

        # ── SQL transformations ───────────────────────────────────────────────
        for table_id, sql in sql_transformations.items():
            sql_blob_path = f"{base_prefix}/sql/{table_id}.sql"
            blob = bucket.blob(sql_blob_path)
            blob.upload_from_string(sql, content_type="text/plain")
            uploaded.append(f"gs://{bucket_name}/{sql_blob_path}")

        # ── DQ validations ────────────────────────────────────────────────────
        for table_id, dq_sql in dq_validations.items():
            dq_blob_path = f"{base_prefix}/dq/{table_id}_dq_checks.sql"
            blob = bucket.blob(dq_blob_path)
            blob.upload_from_string(dq_sql, content_type="text/plain")
            uploaded.append(f"gs://{bucket_name}/{dq_blob_path}")

        # ── Column mapping ────────────────────────────────────────────────────
        mapping = _build_column_mapping(metadata_config)
        mapping_blob_path = f"{base_prefix}/mapping/column_mapping.json"
        blob = bucket.blob(mapping_blob_path)
        blob.upload_from_string(
            json.dumps(mapping, indent=2),
            content_type="application/json",
        )
        uploaded.append(f"gs://{bucket_name}/{mapping_blob_path}")

        # ── Manifest ──────────────────────────────────────────────────────────
        manifest = {
            "run_timestamp": ts,
            "project_id": project_id,
            "gold_dataset": gold_dataset_id,
            "base_gcs_prefix": f"gs://{bucket_name}/{base_prefix}",
            "artifacts": uploaded,
        }
        manifest_path = f"{base_prefix}/manifest.json"
        blob = bucket.blob(manifest_path)
        blob.upload_from_string(
            json.dumps(manifest, indent=2),
            content_type="application/json",
        )
        uploaded.append(f"gs://{bucket_name}/{manifest_path}")

        return {
            "success": True,
            "bucket": bucket_name,
            "base_prefix": f"gs://{bucket_name}/{base_prefix}",
            "uploaded_files": uploaded,
            "file_count": len(uploaded),
            "message": (
                f"Successfully uploaded {len(uploaded)} files to "
                f"gs://{bucket_name}/{base_prefix}/"
            ),
        }

    except GoogleAPIError as e:
        logger.error("GCS upload error: %s", e)
        return {"success": False, "uploaded_files": [], "message": str(e)}
    except Exception as e:
        logger.error("Unexpected GCS error: %s", e)
        return {"success": False, "uploaded_files": [], "message": str(e)}


def _build_column_mapping(metadata_config: dict) -> dict:
    mapping = {
        "project_id": metadata_config.get("project_id"),
        "silver_dataset": metadata_config.get("silver_dataset"),
        "gold_dataset": metadata_config.get("gold_dataset"),
        "generated_at": metadata_config.get("generated_at"),
        "table_mappings": [],
    }
    for table in metadata_config.get("tables", []):
        mapping["table_mappings"].append(
            {
                "source": f"{metadata_config['silver_dataset']}.{table['source_table_id']}",
                "target": f"{metadata_config['gold_dataset']}.{table['gold_table_id']}",
                "role": table.get("role"),
                "partitioning": table.get("partitioning"),
                "clustering": table.get("clustering"),
            }
        )
    return mapping