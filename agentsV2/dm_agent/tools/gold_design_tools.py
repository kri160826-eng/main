"""
Gold layer design tool.
Generates the full Gold-layer data model, SQL transformations, and metadata
based on the analysed Silver-layer schema information.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .gcs_tools import store_design

logger = logging.getLogger(__name__)


# ── Template helpers ──────────────────────────────────────────────────────────

def _audit_columns() -> str:
    return """
    -- Audit & lineage columns
    CURRENT_TIMESTAMP()                             AS dw_insert_timestamp,
    CURRENT_TIMESTAMP()                             AS dw_update_timestamp,
    'silver_to_gold_pipeline'                       AS dw_source_system,
    GENERATE_UUID()                                 AS dw_batch_id,
    SESSION_USER()                                  AS dw_created_by"""


def _dq_checks(full_table_id: str, key_col: str = "surrogate_key") -> str:
    return f"""
-- ── Data Quality Validation ────────────────────────────────────────────────
-- Run BEFORE loading Gold layer tables

-- 1. Null check on surrogate key
SELECT COUNT(*) AS null_keys
FROM `{full_table_id}`
WHERE {key_col} IS NULL;

-- 2. Duplicate check
SELECT {key_col}, COUNT(*) AS cnt
FROM `{full_table_id}`
GROUP BY 1
HAVING cnt > 1;

-- 3. Referential integrity (example — update FK column names as needed)
-- SELECT f.fact_key
-- FROM `{full_table_id}` f
-- LEFT JOIN `<project>.<gold_dataset>.dim_<entity>` d USING (dim_key)
-- WHERE d.dim_key IS NULL;

-- 4. Row-count reconciliation
SELECT
  (SELECT COUNT(*) FROM `{full_table_id}`) AS gold_rows,
  -- Replace with Silver source table:
  (SELECT COUNT(*) FROM `<project>.<silver_dataset>.<source_table>`) AS silver_rows;
"""


def _partitioning_recommendation(schema_fields: list[dict]) -> dict:
    """Infer sensible partitioning from schema."""
    date_candidates = [
        f["name"] for f in schema_fields
        if f.get("field_type") in ("DATE", "TIMESTAMP", "DATETIME")
        and any(kw in f["name"].lower() for kw in ("date", "time", "created", "updated", "loaded"))
    ]
    int_candidates = [
        f["name"] for f in schema_fields
        if f.get("field_type") in ("INTEGER", "INT64")
        and any(kw in f["name"].lower() for kw in ("year", "month", "day"))
    ]
    if date_candidates:
        return {"strategy": "TIME", "column": date_candidates[0], "granularity": "DAY"}
    if int_candidates:
        return {"strategy": "RANGE", "column": int_candidates[0]}
    return {"strategy": "INGESTION_TIME", "column": "_PARTITIONTIME"}


def _clustering_recommendation(schema_fields: list[dict]) -> list[str]:
    """Suggest clustering columns (up to 4)."""
    priority_keywords = ["status", "type", "category", "region", "country", "segment", "channel"]
    candidates = [
        f["name"] for f in schema_fields
        if f.get("field_type") in ("STRING", "INTEGER", "INT64")
        and any(kw in f["name"].lower() for kw in priority_keywords)
    ]
    return candidates[:4]


# ── Main tool ─────────────────────────────────────────────────────────────────

def generate_gold_design(
    project_id: str,
    silver_dataset_id: str,
    gold_dataset_id: str,
    silver_schemas: list[dict],
) -> dict:
    """
    Generate a complete Gold-layer design from analysed Silver-layer schemas.

    Args:
        project_id: GCP project ID.
        silver_dataset_id: Source Silver-layer dataset name.
        gold_dataset_id: Target Gold-layer dataset name (will be created if absent).
        silver_schemas: List of schema analysis dicts from analyze_silver_schema().

    Returns:
        dict containing:
            - gold_tables: list of table design specs
            - sql_transformations: dict mapping table_id → SQL string
            - dq_validations: dict mapping table_id → DQ SQL string
            - partitioning_strategy: dict
            - audit_strategy: str
            - metadata_config: dict (for GCS storage)
    """
    gold_tables = []
    sql_transformations = {}
    dq_validations = {}
    metadata_config = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "project_id": project_id,
        "silver_dataset": silver_dataset_id,
        "gold_dataset": gold_dataset_id,
        "tables": [],
    }

    for schema in silver_schemas:
        if not schema.get("success"):
            continue

        table_id = schema["table_id"]
        fields: list[dict] = schema.get("schema", [])
        full_silver = f"{project_id}.{silver_dataset_id}.{table_id}"

        # ── Infer table role (fact vs dimension) ──────────────────────────────
        numeric_cols = [
            f for f in fields
            if f["field_type"] in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC", "INTEGER", "INT64")
            and any(kw in f["name"].lower() for kw in ("amount", "qty", "quantity", "count", "revenue", "cost", "price", "value", "total", "sum"))
        ]
        date_cols = [
            f for f in fields
            if f["field_type"] in ("DATE", "TIMESTAMP", "DATETIME")
        ]
        is_fact = len(numeric_cols) >= 2 or (len(numeric_cols) >= 1 and len(date_cols) >= 1)
        table_role = "fact" if is_fact else "dimension"

        gold_table_id = f"{'fact' if is_fact else 'dim'}_{table_id}"
        full_gold = f"{project_id}.{gold_dataset_id}.{gold_table_id}"

        part_rec = _partitioning_recommendation(fields)
        clust_rec = _clustering_recommendation(fields)

        # ── Build column list ─────────────────────────────────────────────────
        select_cols = []
        for f in fields:
            col = f["name"]
            dtype = f["field_type"]
            # Surrogate key for dimensions
            if table_role == "dimension" and col.lower().endswith("_id"):
                select_cols.append(
                    f"    FARM_FINGERPRINT(CAST({col} AS STRING))      AS {col}_surrogate_key"
                )
            # Standardise dates
            elif dtype in ("TIMESTAMP", "DATETIME"):
                select_cols.append(
                    f"    DATE({col})                                   AS {col}_date,\n"
                    f"    {col}                                         AS {col}"
                )
            else:
                select_cols.append(f"    {col}")

        # SCD Type 2 columns for dimensions
        scd_cols = ""
        if table_role == "dimension":
            scd_cols = """
    -- SCD Type 2 columns
    CURRENT_DATE()          AS eff_start_date,
    DATE('9999-12-31')      AS eff_end_date,
    TRUE                    AS is_current,"""

        # KPI aggregations for facts
        kpi_block = ""
        if table_role == "fact" and numeric_cols:
            kpi_lines = []
            for nc in numeric_cols:
                name = nc["name"]
                kpi_lines.append(f"    SUM({name})             AS total_{name},")
                kpi_lines.append(f"    AVG({name})             AS avg_{name},")
                kpi_lines.append(f"    MAX({name})             AS max_{name},")
            kpi_block = "\n".join(kpi_lines)

        # ── SQL transformation ────────────────────────────────────────────────
        partition_clause = ""
        if part_rec["strategy"] == "TIME":
            partition_clause = f"PARTITION BY {part_rec.get('granularity','DAY')}({part_rec['column']})"
        elif part_rec["strategy"] == "INGESTION_TIME":
            partition_clause = "PARTITION BY DATE(_PARTITIONTIME)"

        cluster_clause = ""
        if clust_rec:
            cluster_clause = f"CLUSTER BY {', '.join(clust_rec)}"

        col_list = ",\n".join(select_cols)
        sql = f"""-- ════════════════════════════════════════════════════════
-- Gold Layer Table: {gold_table_id}
-- Role          : {table_role.upper()}
-- Source        : {full_silver}
-- Generated     : {datetime.now(tz=timezone.utc).isoformat()}
-- ════════════════════════════════════════════════════════

CREATE OR REPLACE TABLE `{full_gold}`
{partition_clause}
{cluster_clause}
OPTIONS (
  description = 'Gold layer {table_role} table sourced from {full_silver}',
  labels      = [('layer', 'gold'), ('role', '{table_role}'), ('source', '{silver_dataset_id}')]
)
AS
WITH silver_base AS (
    SELECT
{col_list}
    FROM `{full_silver}`
    WHERE 1 = 1
      -- Add incremental filter here, e.g.:
      -- AND DATE(updated_at) = CURRENT_DATE() - 1
),
{('enriched AS (' + chr(10) + '    SELECT' + chr(10) + '        *,' + chr(10) + scd_cols + chr(10) + '    FROM silver_base' + chr(10) + '),' if table_role == 'dimension' else 'aggregated AS (' + chr(10) + '    SELECT' + chr(10) + '        *,' + chr(10) + kpi_block + chr(10) + '    FROM silver_base' + chr(10) + '),')}
final AS (
    SELECT
        *,{_audit_columns()}
    FROM {'enriched' if table_role == 'dimension' else 'aggregated'}
)
SELECT * FROM final;
"""

        # ── Aggregation / summary view ────────────────────────────────────────
        agg_view_id = f"vw_{gold_table_id}_summary"
        full_agg_view = f"{project_id}.{gold_dataset_id}.{agg_view_id}"
        agg_sql = ""
        if table_role == "fact" and date_cols and numeric_cols:
            date_col = date_cols[0]["name"]
            metric_col = numeric_cols[0]["name"]
            agg_sql = f"""
-- ── Summary / Aggregation View: {agg_view_id} ─────────────────────────────
CREATE OR REPLACE VIEW `{full_agg_view}`
OPTIONS (description = 'Daily summary of {gold_table_id}')
AS
SELECT
    DATE({date_col})                AS report_date,
    COUNT(*)                        AS record_count,
    SUM({metric_col})               AS total_{metric_col},
    AVG({metric_col})               AS avg_{metric_col},
    MIN({metric_col})               AS min_{metric_col},
    MAX({metric_col})               AS max_{metric_col}
FROM `{full_gold}`
GROUP BY 1
ORDER BY 1 DESC;
"""

        sql_transformations[gold_table_id] = sql + (agg_sql or "")
        dq_validations[gold_table_id] = _dq_checks(full_gold, f"{table_id}_id")

        # ── Table spec ───────────────────────────────────────────────────────
        table_spec = {
            "gold_table_id": gold_table_id,
            "full_table_id": full_gold,
            "source_table": full_silver,
            "role": table_role,
            "columns": [
                {
                    "name": f["name"],
                    "type": f["field_type"],
                    "mode": f["mode"],
                    "description": f.get("description", ""),
                }
                for f in fields
            ],
            "partitioning": part_rec,
            "clustering": clust_rec,
            "scd_type": "SCD2" if table_role == "dimension" else None,
            "kpi_columns": [nc["name"] for nc in numeric_cols],
            "aggregation_view": agg_view_id if table_role == "fact" else None,
        }
        gold_tables.append(table_spec)
        metadata_config["tables"].append(
            {
                "gold_table_id": gold_table_id,
                "source_table_id": table_id,
                "role": table_role,
                "partitioning": part_rec,
                "clustering": clust_rec,
            }
        )

    # ── Lineage DDL ───────────────────────────────────────────────────────────
    lineage_sql = f"""
-- ── Lineage Metadata Table ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `{project_id}.{gold_dataset_id}.lineage_log`
(
    run_id          STRING        NOT NULL,
    source_table    STRING        NOT NULL,
    target_table    STRING        NOT NULL,
    rows_processed  INT64,
    run_started_at  TIMESTAMP,
    run_completed_at TIMESTAMP,
    status          STRING,
    error_message   STRING,
    pipeline_name   STRING,
    batch_id        STRING,
    dw_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(dw_insert_timestamp)
CLUSTER BY source_table, target_table, status
OPTIONS (description = 'Gold layer pipeline lineage and audit log');
"""
    sql_transformations["lineage_log"] = lineage_sql

    design = {
        "success": True,
        "gold_dataset_id": gold_dataset_id,
        "gold_tables": gold_tables,
        "table_count": len(gold_tables),
        "sql_transformations": sql_transformations,
        "dq_validations": dq_validations,
        "audit_strategy": (
            "All Gold tables include: dw_insert_timestamp, dw_update_timestamp, "
            "dw_source_system, dw_batch_id, dw_created_by. "
            "Pipeline runs logged to lineage_log table. "
            "Dimensions implement SCD Type 2 with eff_start_date, eff_end_date, is_current."
        ),
        "metadata_config": metadata_config,
    }

    # Cache the full design so create_gcs_config() can retrieve it without
    # Gemini having to pass the large payload as a function argument.
    design_id = str(uuid.uuid4())
    store_design(design_id, design)

    return {
        "success": True,
        "design_id": design_id,
        "gold_dataset_id": gold_dataset_id,
        "table_count": len(gold_tables),
        "gold_table_ids": [t["gold_table_id"] for t in gold_tables],
        "audit_strategy": design["audit_strategy"],
        "message": (
            f"Gold design generated for {len(gold_tables)} tables. "
            f"Call create_gcs_config with design_id='{design_id}' to upload."
        ),
    }