"""Build an ERD from a Proposal.

Two representations:
  * ``build_mermaid`` - Mermaid ``erDiagram`` text (saved as the .mmd artifact).
  * ``build_svg`` / ``render_svg_page`` - a fully self-contained SVG diagram
    generated directly from the model. This needs no external scripts/CDN, so
    it renders reliably (and keeps the schema inside your environment).
"""

from __future__ import annotations

import html
import re
from collections import defaultdict

from .models import Proposal, Table


def _mermaid_type(bq_type: str) -> str:
    # Mermaid erDiagram type tokens must be single words.
    return re.sub(r"[^A-Za-z0-9_]", "_", (bq_type or "STRING"))


def _entity_block(table: Table) -> str:
    lines = [f"    {table.name} {{"]
    for col in table.columns:
        key = ""
        if col.is_primary_key:
            key = "PK"
        elif any(col.name == fk.column for fk in table.foreign_keys):
            key = "FK"
        lines.append(f"        {_mermaid_type(col.type)} {col.name} {key}".rstrip())
    lines.append("    }")
    return "\n".join(lines)


def build_mermaid(proposal: Proposal) -> str:
    """Return a Mermaid erDiagram definition string."""
    parts = ["erDiagram"]
    for table in proposal.all_tables:
        parts.append(_entity_block(table))

    seen: set[tuple[str, str]] = set()
    for rel in proposal.relationships:
        pair = (rel.from_table, rel.to_table)
        if pair in seen:
            continue
        seen.add(pair)
        # fact many-to-one dimension  =>  dim ||--o{ fact
        parts.append(
            f'    {rel.to_table} ||--o{{ {rel.from_table} : "{rel.from_column}"'
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Self-contained SVG ERD (no external dependencies)
# ---------------------------------------------------------------------------

def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _truncate(text: object, limit: int) -> str:
    s = str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# Layout constants
_COL_W = 250
_GAP_X = 120
_ROW_H = 19
_HEADER_H = 30
_PAD = 28
_BOX_GAP = 34
_MAX_ROWS = 16


def _box_height(table: Table) -> int:
    shown = min(len(table.columns), _MAX_ROWS)
    extra = 1 if len(table.columns) > _MAX_ROWS else 0
    return _HEADER_H + max(shown + extra, 1) * _ROW_H + 8


def build_svg(proposal: Proposal) -> str:
    """Render the star schema as a standalone SVG string."""
    facts = list(proposal.facts)
    dims = list(proposal.dimensions)

    # Star layout: facts in the centre column, dimensions split left / right.
    left = dims[0::2]
    right = dims[1::2]
    columns = [left, facts, right]
    xs = [_PAD, _PAD + _COL_W + _GAP_X, _PAD + 2 * (_COL_W + _GAP_X)]

    col_totals = []
    for col in columns:
        h = sum(_box_height(t) for t in col) + _BOX_GAP * max(len(col) - 1, 0)
        col_totals.append(h)

    content_h = max(col_totals) if col_totals else 100
    total_h = content_h + _PAD * 2
    total_w = _PAD * 2 + 3 * _COL_W + 2 * _GAP_X

    # Position each box; vertically centre each column.
    pos: dict[str, tuple[float, float, int, int]] = {}
    for ci, col in enumerate(columns):
        y = _PAD + max(0, (content_h - col_totals[ci]) / 2)
        for t in col:
            h = _box_height(t)
            pos[t.name] = (xs[ci], y, _COL_W, h)
            y += h + _BOX_GAP

    fk_cols = {t.name: {fk.column for fk in t.foreign_keys} for t in proposal.all_tables}

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}" font-family="Segoe UI, Arial, sans-serif">',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
        'orient="auto" markerUnits="strokeWidth">'
        '<path d="M0,0 L8,3 L0,6 Z" fill="#64748b"/></marker></defs>',
        f'<rect x="0" y="0" width="{total_w}" height="{total_h}" fill="#ffffff"/>',
    ]

    # --- Relationship edges (drawn first, so boxes sit on top) --------------
    by_fact_side: dict[tuple[str, str], list] = defaultdict(list)
    for rel in proposal.relationships:
        if rel.from_table not in pos or rel.to_table not in pos:
            continue
        fx = pos[rel.from_table][0]
        dx = pos[rel.to_table][0]
        side = "left" if dx < fx else "right"
        by_fact_side[(rel.from_table, side)].append(rel)

    for (fact, side), rels in by_fact_side.items():
        fx, fy, fw, fh = pos[fact]
        k = len(rels)
        for i, rel in enumerate(rels):
            dx, dy, dw, dh = pos[rel.to_table]
            sy = fy + fh * (i + 1) / (k + 1)
            ey = dy + dh / 2
            if side == "left":
                sx, ex = fx, dx + dw
                c1x, c2x = sx - 60, ex + 60
            else:
                sx, ex = fx + fw, dx
                c1x, c2x = sx + 60, ex - 60
            svg.append(
                f'<path d="M{sx:.0f},{sy:.0f} C{c1x:.0f},{sy:.0f} {c2x:.0f},{ey:.0f} '
                f'{ex:.0f},{ey:.0f}" fill="none" stroke="#94a3b8" stroke-width="1.5" '
                f'marker-end="url(#arrow)"/>'
            )

    # --- Entity boxes -------------------------------------------------------
    for table in proposal.all_tables:
        x, y, w, h = pos[table.name]
        is_fact = table.table_type.value == "fact"
        header_fill = "#4f46e5" if is_fact else "#0f766e"
        icon = "⭐" if is_fact else "\U0001f537"

        svg.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" ry="8" '
            f'fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2"/>'
        )
        svg.append(
            f'<path d="M{x},{y+8} q0,-8 8,-8 h{w-16} q8,0 8,8 v{_HEADER_H-8} h{-w} z" '
            f'fill="{header_fill}"/>'
        )
        svg.append(
            f'<text x="{x+10}" y="{y+20}" fill="#ffffff" font-size="13" '
            f'font-weight="700">{icon} {_esc(_truncate(table.name, 26))}</text>'
        )

        row_y = y + _HEADER_H + 14
        shown = table.columns[:_MAX_ROWS]
        for col in shown:
            if col.is_primary_key:
                badge, bcolor = "PK", "#b45309"
            elif col.name in fk_cols.get(table.name, set()):
                badge, bcolor = "FK", "#2563eb"
            elif col.is_measure:
                badge, bcolor = "M", "#059669"
            else:
                badge, bcolor = "", "#64748b"

            if badge:
                svg.append(
                    f'<text x="{x+10}" y="{row_y}" fill="{bcolor}" font-size="10" '
                    f'font-weight="700">{badge}</text>'
                )
            label = _truncate(f"{col.name} : {col.type}", 34)
            svg.append(
                f'<text x="{x+34}" y="{row_y}" fill="#0f172a" font-size="11">'
                f'{_esc(label)}</text>'
            )
            row_y += _ROW_H

        if len(table.columns) > _MAX_ROWS:
            more = len(table.columns) - _MAX_ROWS
            svg.append(
                f'<text x="{x+34}" y="{row_y}" fill="#94a3b8" font-size="11" '
                f'font-style="italic">… {more} more column(s)</text>'
            )

    svg.append("</svg>")
    return "\n".join(svg)


def render_svg_page(svg: str) -> str:
    """Wrap the SVG in a minimal scrollable HTML page for st.components."""
    return (
        '<div style="overflow:auto; background:#ffffff; border:1px solid #e2e8f0; '
        f'border-radius:8px; padding:8px;">{svg}</div>'
    )


def svg_pixel_height(proposal: Proposal) -> int:
    """Best-effort rendered height so the Streamlit component sizes correctly."""
    facts = list(proposal.facts)
    dims = list(proposal.dimensions)
    columns = [dims[0::2], facts, dims[1::2]]
    totals = [
        sum(_box_height(t) for t in col) + _BOX_GAP * max(len(col) - 1, 0)
        for col in columns
    ]
    return int((max(totals) if totals else 100) + _PAD * 2 + 24)


def render_html(mermaid_src: str, height: int = 620) -> str:
    """Wrap a Mermaid definition in a self-contained HTML page for st.components."""
    return f"""
<!DOCTYPE html>
<html>
  <head>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
      body {{ margin: 0; background: white; }}
      .mermaid {{ font-size: 14px; }}
    </style>
  </head>
  <body>
    <div class="mermaid">{mermaid_src}</div>
    <script>
      mermaid.initialize({{ startOnLoad: true, theme: "default", securityLevel: "loose" }});
    </script>
  </body>
</html>
"""
