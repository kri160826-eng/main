"""Design a Gold-layer star schema from Silver metadata.

Two strategies:
  * LLMModeler  - uses Google Gemini (Flash) to reason about entities, facts,
                  dimensions, keys and relationships. Preferred.
  * HeuristicModeler - a deterministic fallback used when no Gemini API key
                  is configured, so the app is fully usable offline.

Both return a validated `Proposal`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .analyzer import summarize_for_prompt
from .models import (
    Column,
    ExcludedTable,
    ForeignKey,
    Partitioning,
    PartitionType,
    Proposal,
    Relationship,
    Table,
    TableType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-powered modeler
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a senior analytics engineer and data modeler. You \
design Gold-layer dimensional models (Kimball star schemas) in Google \
BigQuery from a Silver (cleaned/normalised) layer.

You will receive JSON metadata describing Silver tables: names, columns with \
BigQuery types, row counts, any declared constraints, and a few sample rows.

Design a star schema for the Gold layer. Follow these rules:
- CONSIDER EVERY TABLE in the provided metadata. Each Silver table must EITHER \
contribute to at least one Gold table (named in that Gold table's \
source_tables) OR appear in "excluded_tables" with a concrete reason. Never \
silently drop a table. It is fine to merge several Silver tables into one \
dimension (denormalisation) or to combine lookups - just make sure every \
source table is accounted for somewhere.
- Identify business processes and grain. Create one or more FACT tables at a \
clear, stated grain. Create conformed DIMENSION tables around them.
- Give every dimension a surrogate primary key named <entity>_key of type \
INT64, plus the natural/business key from Silver.
- Facts reference dimensions via <entity>_key foreign keys. Facts hold \
additive/numeric measures and degenerate dimensions where appropriate.
- Use BigQuery Standard SQL types (STRING, INT64, FLOAT64, NUMERIC, BOOL, \
DATE, DATETIME, TIMESTAMP, etc.).
- Recommend partitioning and clustering. IMPORTANT: only set \
partitioning.type="time" on a column whose Gold type is DATE, DATETIME or \
TIMESTAMP. If a fact's only date reference is an INT64 date key (e.g. \
YYYYMMDD) or a dimension surrogate key, DO NOT time-partition it - use \
clustering on that key instead (or add a real DATE/TIMESTAMP column). Never \
wrap an INT64 column in DATE()/TIMESTAMP() in load_sql.
- Provide column-level lineage (source_table, source_column) and, when a \
value must be derived, a BigQuery source_expression.
- For each table, provide a load_sql: the SELECT body (without CREATE) that \
transforms Silver into that Gold table. For dimensions, generate the \
surrogate key using ROW_NUMBER() OVER (ORDER BY <business_key>). For facts, \
join to the dimensions to resolve surrogate foreign keys.
- Explain WHY each table exists in its 'rationale'.

Return ONLY a single JSON object, no prose, matching this shape:
{
  "summary": "string",
  "data_quality_assumptions": ["string"],
  "transformation_notes": "string",
  "excluded_tables": [ {"table":"ds.table","reason":"why it was left out"} ],
  "dimensions": [ { "name","description","rationale","grain",
      "source_tables":["ds.table"], "primary_key":"x_key",
      "columns":[{"name","type","description","nullable","is_primary_key",
                  "is_business_key","is_measure","source_table","source_column",
                  "source_expression"}],
      "partitioning":{"type":"none|time|integer_range","column","granularity"},
      "clustering":["col"], "load_sql":"SELECT ..." } ],
  "facts": [ { ...same shape...,
      "foreign_keys":[{"column","references_table","references_column"}],
      "partitioning":{"type":"time","column":"...","granularity":"DAY"} } ],
  "relationships": [ {"from_table","from_column","to_table","to_column",
                      "cardinality":"many-to-one"} ]
}"""


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model response did not contain a JSON object.")
    return json.loads(text[start : end + 1])


class LLMModeler:
    """Star-schema modeler backed by Google Gemini (Flash by default).

    Supports two backends via the google-genai SDK:
      * Vertex AI  (use_vertex=True) - auth via ADC / service account.
      * AI Studio  (api_key=...)     - auth via an API key.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        use_vertex: bool = False,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        from google import genai  # imported lazily so the app works without the lib

        self._genai = genai
        if use_vertex:
            self._client = genai.Client(
                vertexai=True, project=project, location=location or "us-central1"
            )
        else:
            self._client = genai.Client(api_key=api_key)
        self._model = model

    def design(
        self,
        metadata: dict[str, Any],
        gold_dataset: str,
        business_domain: str | None,
    ) -> Proposal:
        compact = summarize_for_prompt(metadata)
        table_names = [t["name"] for t in compact.get("tables", [])]
        user_content = (
            f"Business domain / reporting purpose: {business_domain or 'not specified'}\n"
            f"Target Gold dataset name: {gold_dataset}\n"
            f"The Silver dataset has {len(table_names)} tables that you MUST all "
            f"account for (as sources or in excluded_tables): {table_names}\n\n"
            f"Silver metadata JSON:\n{json.dumps(compact, default=str)}"
        )
        logger.info("Requesting star-schema design from Gemini model '%s'...", self._model)
        resp = self._client.models.generate_content(
            model=self._model,
            contents=user_content,
            config=self._build_config(),
        )
        text = resp.text or ""
        if not text.strip():
            # Gemini 2.5 "thinking" models can burn the whole output budget on
            # reasoning and return no content; surface the finish reason.
            reason = self._diagnose_empty(resp)
            raise ValueError(
                "Gemini returned an empty response"
                + (f" ({reason})" if reason else "")
                + ". Try selecting fewer tables or raising GEMINI_MAX_OUTPUT_TOKENS."
            )
        raw = _extract_json(text)
        return _build_proposal(
            raw,
            gold_dataset=gold_dataset,
            source_project=metadata["project"],
            source_dataset=metadata["dataset"],
            business_domain=business_domain,
        )

    def _build_config(self):
        """Build the generation config.

        Gemini 2.5 models spend part of ``max_output_tokens`` on internal
        "thinking". For deterministic JSON extraction we disable thinking so the
        full budget is available for the response, and use a generous ceiling.
        """
        import os

        types = self._genai.types
        max_tokens = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "32768"))
        kwargs: dict[str, Any] = dict(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )
        # ThinkingConfig only exists on newer SDKs / 2.5 models; guard for both.
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:  # older SDK or non-thinking model
            pass
        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _diagnose_empty(resp) -> str:
        try:
            cand = resp.candidates[0]
            reason = getattr(cand, "finish_reason", None)
            return f"finish_reason={reason}" if reason else ""
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Heuristic modeler (offline fallback)
# ---------------------------------------------------------------------------

_MEASURE_HINTS = ("amount", "total", "qty", "quantity", "price", "cost", "count", "revenue", "value", "sum")
_DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
_NUMERIC_TYPES = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"}


def _looks_like_fk(col_name: str) -> bool:
    n = col_name.lower()
    return n.endswith("_id") or n.endswith("_key") or n.endswith("_fk")


def _guess_business_key(names: list[str], entity: str) -> str:
    """Pick the natural/business key column for a dimension."""
    for candidate in (f"{entity}_id", f"{entity}_key", "id"):
        for n in names:
            if n.lower() == candidate:
                return n
    return names[0] if names else "id"


class HeuristicModeler:
    """A simple, transparent rule-based modeler used when no LLM is available.

    Classification: a table is treated as a FACT if it has date/timestamp
    columns and multiple foreign-key-like columns or numeric measures;
    otherwise it becomes a DIMENSION. This is a reasonable starting point that
    the user is expected to review before approving.
    """

    def design(
        self,
        metadata: dict[str, Any],
        gold_dataset: str,
        business_domain: str | None,
    ) -> Proposal:
        logger.info("Using heuristic modeler (no LLM configured).")
        source_dataset = metadata["dataset"]
        dims: list[Table] = []
        fact_specs: list[dict] = []
        relationships: list[Relationship] = []
        # entity -> business-key column name, used to wire fact FK joins.
        dim_business_keys: dict[str, str] = {}

        # First pass: classify tables and build dimensions.
        for t in metadata["tables"]:
            cols = t["columns"]
            names = [c["name"] for c in cols]
            fk_cols = [n for n in names if _looks_like_fk(n)]
            date_cols = [c["name"] for c in cols if c["type"].upper() in _DATE_TYPES]
            measure_cols = [
                c["name"]
                for c in cols
                if c["type"].upper() in _NUMERIC_TYPES
                and any(h in c["name"].lower() for h in _MEASURE_HINTS)
            ]
            is_fact = bool(date_cols) and (len(fk_cols) >= 2 or bool(measure_cols))

            entity = re.sub(r"^(dim_|fact_|stg_|silver_)", "", t["name"]).rstrip("s") or t["name"]
            src = f"{source_dataset}.{t['name']}"

            if is_fact:
                fact_specs.append(
                    {
                        "t": t,
                        "entity": entity,
                        "src": src,
                        "fk_cols": fk_cols,
                        "date_cols": date_cols,
                        "measure_cols": measure_cols,
                    }
                )
            else:
                bk = _guess_business_key(names, entity)
                dim_business_keys[entity] = bk
                dims.append(self._build_dim(t, entity, src, bk))

        # Second pass: build facts, wiring FKs only to dimensions that exist.
        facts: list[Table] = [
            self._build_fact(
                spec["t"],
                spec["entity"],
                spec["src"],
                spec["fk_cols"],
                spec["date_cols"],
                spec["measure_cols"],
                dim_business_keys,
            )
            for spec in fact_specs
        ]

        # Derive relationships from the (already validated) fact foreign keys.
        for f in facts:
            for fk in f.foreign_keys:
                relationships.append(
                    Relationship(
                        from_table=f.name,
                        from_column=fk.column,
                        to_table=fk.references_table,
                        to_column=fk.references_column,
                    )
                )

        return Proposal(
            gold_dataset=gold_dataset,
            source_project=metadata["project"],
            source_dataset=source_dataset,
            business_domain=business_domain,
            summary=(
                f"Heuristic star schema with {len(facts)} fact and {len(dims)} "
                f"dimension table(s). Review carefully before approving."
            ),
            dimensions=dims,
            facts=facts,
            relationships=relationships,
            data_quality_assumptions=[
                "Business keys in Silver are non-null and unique per entity.",
                "Date/timestamp columns are valid and used for partitioning.",
                "Numeric measure columns are additive unless noted.",
            ],
            transformation_notes=(
                "Generated by the heuristic modeler. Surrogate keys use "
                "ROW_NUMBER(); verify grain and join logic before loading."
            ),
        )

    def _build_dim(self, t: dict, entity: str, src: str, bk: str) -> Table:
        pk = f"{entity}_key"
        columns = [
            Column(
                name=pk,
                type="INT64",
                description="Surrogate key",
                nullable=False,
                is_primary_key=True,
                source_expression=f"ROW_NUMBER() OVER (ORDER BY {bk})",
            )
        ]
        for c in t["columns"]:
            columns.append(
                Column(
                    name=c["name"],
                    type=c["type"],
                    description=c.get("description"),
                    nullable=c.get("mode") != "REQUIRED",
                    is_business_key=(c["name"] == bk),
                    source_table=src,
                    source_column=c["name"],
                )
            )
        return Table(
            name=f"dim_{entity}",
            table_type=TableType.DIMENSION,
            description=f"Dimension for {entity}.",
            rationale=f"'{t['name']}' looks like a reference/lookup entity, modeled as a conformed dimension.",
            grain=f"one row per {entity}",
            source_tables=[src],
            primary_key=pk,
            columns=columns,
            clustering=[bk],
        )

    def _build_fact(
        self,
        t: dict,
        entity: str,
        src: str,
        fk_cols: list[str],
        date_cols: list[str],
        measure_cols: list[str],
        dim_business_keys: dict[str, str],
    ) -> Table:
        pk = f"{entity}_key"
        order_by = date_cols[0] if date_cols else (fk_cols[0] if fk_cols else "1")
        columns = [
            Column(
                name=pk,
                type="INT64",
                description="Surrogate fact key",
                nullable=False,
                is_primary_key=True,
                source_expression=f"ROW_NUMBER() OVER (ORDER BY {order_by})",
            )
        ]
        foreign_keys: list[ForeignKey] = []
        for c in t["columns"]:
            name = c["name"]
            ref_entity = re.sub(r"(_id|_key|_fk)$", "", name.lower())
            # A FK column that maps to an existing dimension (and is not this
            # fact's own natural key) becomes an INT64 surrogate-key column,
            # resolved by joining the dimension on its business key.
            if name in fk_cols and ref_entity in dim_business_keys and ref_entity != entity:
                ref_table = f"dim_{ref_entity}"
                surrogate = f"{ref_entity}_key"
                columns.append(
                    Column(
                        name=surrogate,
                        type="INT64",
                        description=f"Surrogate FK to {ref_table}",
                        source_expression=f"{ref_table}.{surrogate}",
                    )
                )
                foreign_keys.append(
                    ForeignKey(
                        column=surrogate,
                        references_table=ref_table,
                        references_column=surrogate,
                        source_column=name,
                        references_business_key=dim_business_keys[ref_entity],
                    )
                )
            else:
                # Keep as a regular measure / degenerate-dimension column.
                columns.append(
                    Column(
                        name=name,
                        type=c["type"],
                        description=c.get("description"),
                        nullable=c.get("mode") != "REQUIRED",
                        is_measure=name in measure_cols,
                        source_table=src,
                        source_column=name,
                    )
                )
        part_col = date_cols[0] if date_cols else None
        partitioning = (
            Partitioning(type=PartitionType.TIME, column=part_col, granularity="DAY")
            if part_col
            else Partitioning(type=PartitionType.NONE)
        )
        return Table(
            name=f"fact_{entity}",
            table_type=TableType.FACT,
            description=f"Fact table for the {entity} process.",
            rationale=f"'{t['name']}' has date and key/measure columns, indicating a business event.",
            grain=f"one row per {t['name']} record",
            source_tables=[src],
            primary_key=pk,
            columns=columns,
            foreign_keys=foreign_keys,
            partitioning=partitioning,
            clustering=[fk.column for fk in foreign_keys][:4],
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_proposal(
    raw: dict[str, Any],
    gold_dataset: str,
    source_project: str,
    source_dataset: str,
    business_domain: str | None,
) -> Proposal:
    """Validate a raw dict (from the LLM) into a Proposal."""

    def build_table(d: dict[str, Any], table_type: TableType) -> Table:
        d = dict(d)
        d["table_type"] = table_type
        return Table.model_validate(d)

    dimensions = [build_table(d, TableType.DIMENSION) for d in raw.get("dimensions", [])]
    facts = [build_table(f, TableType.FACT) for f in raw.get("facts", [])]
    relationships = [Relationship.model_validate(r) for r in raw.get("relationships", [])]
    excluded = [ExcludedTable.model_validate(e) for e in raw.get("excluded_tables", [])]

    proposal = Proposal(
        gold_dataset=gold_dataset,
        source_project=source_project,
        source_dataset=source_dataset,
        business_domain=business_domain,
        summary=raw.get("summary", ""),
        dimensions=dimensions,
        facts=facts,
        relationships=relationships,
        excluded_tables=excluded,
        data_quality_assumptions=raw.get("data_quality_assumptions", []),
        transformation_notes=raw.get("transformation_notes"),
    )
    if not proposal.facts and not proposal.dimensions:
        raise ValueError("The generated proposal contains no tables.")
    return proposal


def build_modeler(
    model: str = "gemini-2.5-flash",
    api_key: str | None = None,
    use_vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
):
    """Factory: return a Gemini modeler if configured, else the heuristic one.

    Prefers Vertex AI when ``use_vertex`` is set; otherwise uses an API key.
    Falls back to the heuristic modeler if neither is available or the SDK is
    missing.
    """
    if use_vertex or api_key:
        try:
            return LLMModeler(
                model=model,
                api_key=api_key,
                use_vertex=use_vertex,
                project=project,
                location=location,
            )
        except Exception as exc:  # e.g. google-genai not installed / bad config
            logger.warning("Falling back to heuristic modeler: %s", exc)
    return HeuristicModeler()
