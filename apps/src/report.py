"""Build the human-readable summary report and the artifact bundle."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import ddl_generator, erd, transform_generator
from .models import Proposal


def source_coverage(metadata: dict, proposal: Proposal) -> dict:
    """Compare Silver tables against those actually used by the Gold model.

    Returns {"total", "mapped": [...], "unmapped": [...], "excluded": {name: reason}}.
    A Silver table counts as mapped if it appears in any Gold table's
    source_tables (matched on the bare table name).
    """
    silver = [t["name"] for t in metadata.get("tables", [])]
    used: set[str] = set()
    for table in proposal.all_tables:
        for src in table.source_tables:
            used.add(src.split(".")[-1])

    excluded = {e.table.split(".")[-1]: (e.reason or "no reason given") for e in proposal.excluded_tables}

    mapped = [t for t in silver if t in used]
    unmapped = [t for t in silver if t not in used]
    return {
        "total": len(silver),
        "mapped": mapped,
        "unmapped": unmapped,
        "excluded": excluded,
    }


def build_summary_report(proposal: Proposal) -> str:
    """A Markdown summary explaining the proposed Gold model."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# Gold Layer Star-Schema Proposal: `{proposal.gold_dataset}`",
        "",
        f"*Generated: {now}*",
        "",
        f"**Source:** `{proposal.source_project}.{proposal.source_dataset}`  ",
        f"**Business domain:** {proposal.business_domain or 'not specified'}",
        "",
        "## Overview",
        proposal.summary or "_No summary provided._",
        "",
        f"- **Fact tables:** {len(proposal.facts)}",
        f"- **Dimension tables:** {len(proposal.dimensions)}",
        f"- **Relationships:** {len(proposal.relationships)}",
        "",
        "## Fact Tables",
    ]
    for f in proposal.facts:
        lines += [
            f"### `{f.name}`",
            f"- **Grain:** {f.grain or 'n/a'}",
            f"- **Primary key:** {f.primary_key or 'n/a'}",
            f"- **Sources:** {', '.join(f.source_tables) or 'n/a'}",
            f"- **Why:** {f.rationale or 'n/a'}",
            "",
        ]

    lines.append("## Dimension Tables")
    for d in proposal.dimensions:
        lines += [
            f"### `{d.name}`",
            f"- **Grain:** {d.grain or 'n/a'}",
            f"- **Primary key:** {d.primary_key or 'n/a'}",
            f"- **Sources:** {', '.join(d.source_tables) or 'n/a'}",
            f"- **Why:** {d.rationale or 'n/a'}",
            "",
        ]

    lines.append("## Relationships")
    if proposal.relationships:
        for r in proposal.relationships:
            lines.append(
                f"- `{r.from_table}.{r.from_column}` → "
                f"`{r.to_table}.{r.to_column}` ({r.cardinality})"
            )
    else:
        lines.append("_No relationships defined._")

    if proposal.excluded_tables:
        lines += ["", "## Excluded Silver Tables"]
        for e in proposal.excluded_tables:
            lines.append(f"- `{e.table}` — {e.reason or 'no reason given'}")

    lines += ["", "## Data Quality Assumptions"]
    for a in proposal.data_quality_assumptions or ["_None specified._"]:
        lines.append(f"- {a}")

    if proposal.transformation_notes:
        lines += ["", "## Transformation Notes", proposal.transformation_notes]

    return "\n".join(lines)


def build_artifacts(project: str, proposal: Proposal) -> dict[str, str]:
    """Return a mapping of {filename: text_content} for all artifacts."""
    mermaid = erd.build_mermaid(proposal)
    ddl_script, _ = ddl_generator.generate_ddl(project, proposal)
    transform_script, _ = transform_generator.generate_transforms(project, proposal)

    return {
        "proposal.json": json.dumps(proposal.to_json_dict(), indent=2, default=str),
        "erd.mmd": mermaid,
        "erd.svg": erd.build_svg(proposal),
        "gold_ddl.sql": ddl_script,
        "transformations.sql": transform_script,
        "summary_report.md": build_summary_report(proposal),
    }
