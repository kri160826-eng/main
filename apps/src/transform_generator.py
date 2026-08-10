"""Generate Silver -> Gold transformation SQL from a Proposal.

If the modeler supplied a `load_sql` body for a table (the LLM does this), it
is used verbatim inside a CREATE OR REPLACE TABLE ... AS statement. Otherwise
a readable template is generated from the column mappings so the file is still
complete; those templates are clearly marked for review.
"""

from __future__ import annotations

from .models import Proposal, Table


def _fq(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def _select_from_mappings(project: str, proposal: Proposal, table: Table) -> str:
    """Build a SELECT from column lineage when no load_sql was provided."""
    select_lines: list[str] = []
    for col in table.columns:
        if col.source_expression:
            expr = col.source_expression
        elif col.source_column:
            expr = f"src.{col.source_column}"
        else:
            expr = f"CAST(NULL AS {col.type})  -- TODO: define mapping"
        select_lines.append(f"    {expr} AS {col.name}")

    # Pick a FROM: first source table if present.
    if table.source_tables:
        src_table = table.source_tables[0].split(".")[-1]
        from_lines = [f"  FROM {_fq(project, proposal.source_dataset, src_table)} AS src"]
    else:
        from_lines = ["  FROM /* TODO: source table */ src"]

    # Resolve surrogate foreign keys by joining each dimension on its
    # business key. Only foreign keys carrying join lineage are wired here.
    for fk in table.foreign_keys:
        if fk.source_column and fk.references_business_key:
            dim = _fq(project, proposal.gold_dataset, fk.references_table)
            from_lines.append(
                f"  LEFT JOIN {dim} AS {fk.references_table}\n"
                f"    ON {fk.references_table}.{fk.references_business_key} = src.{fk.source_column}"
            )

    note = "  -- NOTE: template generated from column lineage; review joins and grain."
    return "SELECT\n" + ",\n".join(select_lines) + "\n" + note + "\n" + "\n".join(from_lines)


def generate_table_transform(project: str, proposal: Proposal, table: Table) -> str:
    """Reload a Gold table from Silver.

    We load into the table created by the DDL step (via TRUNCATE + INSERT)
    rather than CREATE OR REPLACE, so the table's schema, primary/foreign keys,
    partitioning and clustering are all preserved. BigQuery rejects a
    CREATE OR REPLACE that changes the partitioning/clustering spec.

    The load SELECT is wrapped as a subquery and its columns are re-projected by
    name into the exact table column order, so column ordering in the generated
    or model-provided SELECT does not matter.
    """
    fq = _fq(project, proposal.gold_dataset, table.name)
    if table.load_sql and table.load_sql.strip():
        body = table.load_sql.strip().rstrip(";")
    else:
        body = _select_from_mappings(project, proposal, table)

    col_names = [c.name for c in table.columns]
    insert_cols = ", ".join(col_names)
    projected = ",\n    ".join(col_names)
    indented_body = "\n".join("    " + line for line in body.splitlines())

    return (
        f"TRUNCATE TABLE {fq};\n"
        f"INSERT INTO {fq} ({insert_cols})\n"
        f"SELECT\n    {projected}\n"
        f"FROM (\n{indented_body}\n) AS _src;"
    )


def generate_transforms(project: str, proposal: Proposal) -> tuple[str, list[str]]:
    """Return (full_script, [statements]). Dimensions load before facts."""
    statements: list[str] = []
    blocks: list[str] = [
        "-- =====================================================================",
        f"-- Silver -> Gold transformations for: {proposal.gold_dataset}",
        f"-- Source: {proposal.source_project}.{proposal.source_dataset}",
        "-- Prerequisite: run gold_ddl.sql first (these statements load into the",
        "-- tables it creates, preserving keys/partitioning/clustering).",
        "-- Load order: dimensions first, then facts (to resolve surrogate keys).",
        "-- =====================================================================",
        "",
    ]

    for table in [*proposal.dimensions, *proposal.facts]:
        header = f"-- Load {table.table_type.value}: {table.name}"
        stmt = generate_table_transform(project, proposal, table)
        statements.append(stmt)
        blocks.extend([header, stmt, ""])

    return "\n".join(blocks), statements
