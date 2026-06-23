"""
Streamlit front-end for dm_agent with built-in ERD rendering.

No Graphviz dependency. ERD is rendered using Streamlit HTML/CSS cards,
so it works on Cloud Run without the Graphviz `dot` executable.
"""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from typing import Any

import streamlit as st

import adk_client

st.set_page_config(
    page_title="Gold Layer Multi-Agent Data Model Development",
    page_icon="🛠️",
    layout="wide",
)

FRONTEND_PUBLIC_URL = os.environ.get("FRONTEND_PUBLIC_URL", "").strip()

# ── Session bootstrap ───────────────────────────────────────────────────────
if "user_id" not in st.session_state:
    st.session_state.user_id = f"user-{uuid.uuid4().hex[:8]}"
if "session_id" not in st.session_state:
    st.session_state.session_id = f"session-{uuid.uuid4().hex[:8]}"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_ready" not in st.session_state:
    st.session_state.session_ready = False
if "latest_erd_model" not in st.session_state:
    st.session_state.latest_erd_model = None
if "latest_mermaid_erd" not in st.session_state:
    st.session_state.latest_mermaid_erd = ""
if "agent_trace" not in st.session_state:
    st.session_state.agent_trace = []


def reset_conversation() -> None:
    st.session_state.session_id = f"session-{uuid.uuid4().hex[:8]}"
    st.session_state.messages = []
    st.session_state.session_ready = False
    st.session_state.latest_erd_model = None
    st.session_state.latest_mermaid_erd = ""
    st.session_state.agent_trace = []


# ── ERD parsing helpers ─────────────────────────────────────────────────────
def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Extract valid JSON objects from markdown/code/text."""
    candidates: list[str] = []
    for match in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL):
        candidates.append(match)

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first : last + 1])

    objects: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                objects.append(parsed)
        except json.JSONDecodeError:
            continue
    return objects


def normalize_erd_model(model: dict[str, Any]) -> dict[str, Any] | None:
    """Accept common agent JSON shapes and normalize to tables + relationships."""
    tables = model.get("tables") or model.get("gold_tables") or []
    relationships = model.get("relationships") or []

    if not tables:
        merged = []
        for table_type, key in [("FACT", "fact_tables"), ("DIMENSION", "dimension_tables")]:
            for item in model.get(key, []) or []:
                if isinstance(item, str):
                    merged.append({"name": item, "type": table_type, "columns": []})
                elif isinstance(item, dict):
                    item.setdefault("type", table_type)
                    merged.append(item)
        tables = merged

    if not tables:
        return None

    normalized_tables = []
    for table in tables:
        if isinstance(table, str):
            normalized_tables.append({"name": table, "type": classify_table_type(table), "columns": []})
            continue

        columns = table.get("columns") or []
        normalized_columns = []
        pk_set = set(table.get("primary_keys") or [])
        fk_list = table.get("foreign_keys") or []
        fk_set = set()
        for fk in fk_list:
            if isinstance(fk, str):
                fk_set.add(fk)
            elif isinstance(fk, dict):
                fk_set.add(fk.get("column") or fk.get("source_column") or "")

        for col in columns:
            if isinstance(col, str):
                col_name = col
                normalized_columns.append(
                    {"name": col_name, "datatype": "", "pk": col_name in pk_set, "fk": col_name in fk_set}
                )
            elif isinstance(col, dict):
                col_name = col.get("name") or col.get("column_name") or "column"
                normalized_columns.append(
                    {
                        "name": col_name,
                        "datatype": col.get("datatype") or col.get("type") or col.get("data_type") or "",
                        "pk": bool(col.get("pk") or col.get("primary_key") or col_name in pk_set),
                        "fk": bool(col.get("fk") or col.get("foreign_key") or col_name in fk_set),
                    }
                )

        table_name = table.get("name") or table.get("table_name") or "table"
        table_type = table.get("type") or classify_table_type(table_name)
        normalized_tables.append(
            {
                "name": table_name,
                "type": table_type,
                "columns": normalized_columns,
                "pk": table.get("pk") or table.get("primary_key") or table.get("primary_keys") or list(pk_set),
                "fk": table.get("fk") or table.get("foreign_key") or table.get("foreign_keys") or list(fk_set),
                "surrogate_key": table.get("surrogate_key") or table.get("surrogate_keys") or "",
                "business_key": table.get("business_key") or table.get("business_keys") or table.get("natural_key") or table.get("natural_keys") or "",
                "grain": table.get("grain") or table.get("business_grain") or "",
                "partition_column": table.get("partition_column") or table.get("partitioning") or "",
                "cluster_columns": table.get("cluster_columns") or table.get("clustering") or [],
            }
        )

    normalized_relationships = []
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        source_table = rel.get("source_table") or rel.get("from_table")
        target_table = rel.get("target_table") or rel.get("to_table")
        source_column = rel.get("source_column") or rel.get("from_column") or rel.get("column") or ""
        target_column = rel.get("target_column") or rel.get("to_column") or source_column
        if source_table and target_table:
            normalized_relationships.append(
                {
                    "source_table": source_table,
                    "target_table": target_table,
                    "source_column": source_column,
                    "target_column": target_column,
                }
            )

    return {"tables": normalized_tables, "relationships": normalized_relationships}


def extract_erd_model_from_text(text: str) -> dict[str, Any] | None:
    for obj in extract_json_objects(text):
        normalized = normalize_erd_model(obj)
        if normalized:
            return normalized
    return None


def extract_mermaid_erd(text: str) -> str:
    """Extract Mermaid code generated by the agent from markdown or plain text."""
    fenced = re.search(r"```mermaid\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Common Mermaid diagram starts. Keep this broad so flowchart, graph, ERD,
    # sequenceDiagram, classDiagram, etc. are all supported.
    inline = re.search(
        r"((?:erDiagram|graph\s+(?:TD|TB|BT|RL|LR)|flowchart\s+(?:TD|TB|BT|RL|LR)|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|journey|gantt|pie|mindmap|timeline)\b.*)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return inline.group(1).strip() if inline else ""


def classify_table_type(table_name: str) -> str:
    name = table_name.lower()
    if name.startswith("fact_") or name.endswith("_fact") or "_fact_" in name:
        return "FACT"
    if name.startswith("dim_") or name.endswith("_dim") or "_dim_" in name:
        return "DIMENSION"
    if name.startswith(("ref_", "lookup_", "lkp_", "code_")):
        return "REFERENCE"
    if name.startswith(("bridge_", "xref_")):
        return "BRIDGE"
    if any(x in name for x in ["audit", "lineage", "log"]):
        return "AUDIT"
    return "TABLE"


def type_css_class(table_type: str) -> str:
    table_type = (table_type or "").upper()
    if table_type == "FACT":
        return "fact"
    if table_type == "DIMENSION":
        return "dimension"
    if table_type == "REFERENCE":
        return "reference"
    if table_type == "BRIDGE":
        return "bridge"
    if table_type == "AUDIT":
        return "audit"
    return "table"




def as_text(value: Any) -> str:
    """Render strings/lists/dicts safely for compact UI cells."""
    if value is None or value == "":
        return "N/A"
    if isinstance(value, list):
        if not value:
            return "N/A"
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("column") or item.get("source_column") or item.get("name") or json.dumps(item))
            else:
                parts.append(str(item))
        return ", ".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def pick_table_value(table: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in table and table.get(key) not in (None, "", []):
            return table.get(key)
    return "N/A"


def build_key_mapping_html(model: dict[str, Any]) -> str:
    """Build the Key Mapping Matrix shown along with the ERD."""
    rows = []
    for table in model.get("tables", []):
        name = html.escape(as_text(table.get("name")))
        table_type = html.escape(as_text(table.get("type") or table.get("table_type") or classify_table_type(table.get("name", ""))))
        pk = html.escape(as_text(pick_table_value(table, "pk", "primary_key", "primary_keys")))
        fk = html.escape(as_text(pick_table_value(table, "fk", "foreign_key", "foreign_keys")))
        sk = html.escape(as_text(pick_table_value(table, "surrogate_key", "surrogate_keys")))
        bk = html.escape(as_text(pick_table_value(table, "business_key", "business_keys", "natural_key", "natural_keys")))
        grain = html.escape(as_text(table.get("grain")))
        partition = html.escape(as_text(pick_table_value(table, "partition_column", "partitioning")))
        clustering = html.escape(as_text(pick_table_value(table, "cluster_columns", "clustering")))
        rows.append(
            f"<tr><td><code>{name}</code></td><td>{table_type}</td><td><code>{pk}</code></td>"
            f"<td><code>{fk}</code></td><td>{sk}</td><td><code>{bk}</code></td>"
            f"<td>{grain}</td><td>{partition}</td><td>{clustering}</td></tr>"
        )
    if not rows:
        return ""
    return f"""
    <h4>Key Mapping Matrix</h4>
    <table class="rel-table key-matrix">
      <thead><tr><th>Table</th><th>Type</th><th>PK</th><th>FK</th><th>Surrogate Key</th><th>Business Key</th><th>Grain</th><th>Partitioning</th><th>Clustering</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def text_to_simple_pdf_bytes(title: str, body: str) -> bytes:
    """Create a simple text PDF without external packages."""
    def esc_pdf(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    clean = re.sub(r"<[^>]+>", " ", body)
    clean = html.unescape(clean)
    clean = re.sub(r"[ \t]+", " ", clean).strip()
    raw_lines = [title, ""] + clean.splitlines()
    lines = []
    width = 92
    for paragraph in raw_lines:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = (current + " " + word).strip()
        if current:
            lines.append(current)

    pages = []
    for i in range(0, max(len(lines), 1), 42):
        chunk = lines[i:i + 42] or [title]
        y = 760
        content = ["BT", "/F1 10 Tf"]
        for line in chunk:
            content.append(f"50 {y} Td ({esc_pdf(line[:120])}) Tj")
            content.append("-50 -16 Td")
            y -= 16
        content.append("ET")
        pages.append("\n".join(content))

    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kid_refs = " ".join(f"{3 + i*2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kid_refs}] /Count {len(pages)} >>")
    font_obj_num = 3 + len(pages)*2
    for idx, content in enumerate(pages):
        page_obj_num = 3 + idx*2
        stream_obj_num = page_obj_num + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_obj_num} 0 R >> >> /Contents {stream_obj_num} 0 R >>")
        encoded = content.encode("latin-1", errors="replace")
        objects.append(f"<< /Length {len(encoded)} >>\nstream\n{content}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = ["%PDF-1.4"]
    offsets = []
    cursor = len(pdf[0].encode("latin-1")) + 1
    for i, obj in enumerate(objects, 1):
        offsets.append(cursor)
        entry = f"{i} 0 obj\n{obj}\nendobj"
        pdf.append(entry)
        cursor += len(entry.encode("latin-1", errors="replace")) + 1
    xref_pos = cursor
    pdf.append(f"xref\n0 {len(objects)+1}\n0000000000 65535 f ")
    for off in offsets:
        pdf.append(f"{off:010d} 00000 n ")
    pdf.append(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF")
    return "\n".join(pdf).encode("latin-1", errors="replace")

def build_erd_html(model: dict[str, Any]) -> str:
    cards = []
    for table in model.get("tables", []):
        name = html.escape(table.get("name", "table"))
        table_type = (table.get("type") or classify_table_type(name)).upper()
        css_class = type_css_class(table_type)
        rows = []
        for col in table.get("columns", []):
            col_name = html.escape(str(col.get("name", "")))
            datatype = html.escape(str(col.get("datatype", "")))
            badges = ""
            if col.get("pk"):
                badges += '<span class="badge pk">PK</span>'
            if col.get("fk"):
                badges += '<span class="badge fk">FK</span>'
            rows.append(f'<div class="col"><span>{col_name}</span><small>{datatype} {badges}</small></div>')
        if not rows:
            rows.append('<div class="col"><span>No columns in JSON</span></div>')
        cards.append(
            f'''
            <div class="card {css_class}">
              <div class="card-title">{name}</div>
              <div class="card-type">{html.escape(table_type)}</div>
              <div class="cols">{''.join(rows)}</div>
            </div>
            '''
        )

    rel_rows = []
    for rel in model.get("relationships", []):
        rel_rows.append(
            f"<tr><td>{html.escape(str(rel.get('source_table','')))}</td>"
            f"<td>{html.escape(str(rel.get('source_column','')))}</td>"
            f"<td>→</td>"
            f"<td>{html.escape(str(rel.get('target_table','')))}</td>"
            f"<td>{html.escape(str(rel.get('target_column','')))}</td></tr>"
        )
    rel_table = ""
    if rel_rows:
        rel_table = f'''
        <h4>Relationships</h4>
        <table class="rel-table">
          <thead><tr><th>Source Table</th><th>Source Column</th><th></th><th>Target Table</th><th>Target Column</th></tr></thead>
          <tbody>{''.join(rel_rows)}</tbody>
        </table>
        '''

    return f'''
    <style>
      .erd-grid {{display:grid; grid-template-columns:repeat(auto-fit,minmax(270px,1fr)); gap:16px; margin-top:10px;}}
      .card {{border-radius:14px; padding:14px; border:2px solid #ddd; box-shadow:0 1px 4px rgba(0,0,0,.08);}}
      .card-title {{font-weight:800; font-size:17px; margin-bottom:3px; word-break:break-word;}}
      .card-type {{font-size:12px; font-weight:700; opacity:.75; margin-bottom:10px;}}
      .cols {{background:rgba(255,255,255,.55); border-radius:10px; padding:8px;}}
      .col {{display:flex; justify-content:space-between; gap:8px; border-bottom:1px solid rgba(0,0,0,.08); padding:5px 0; font-size:13px;}}
      .col:last-child {{border-bottom:none;}}
      .col small {{opacity:.8; text-align:right;}}
      .badge {{font-size:10px; padding:1px 5px; border-radius:8px; margin-left:3px; font-weight:800;}}
      .pk {{background:#fff3cd; color:#6b4e00;}}
      .fk {{background:#e2e3ff; color:#282a7a;}}
      .fact {{background:#d6eaf8; border-color:#1b4f72;}}
      .dimension {{background:#d5f5e3; border-color:#1e8449;}}
      .reference {{background:#fcf3cf; border-color:#b7950b;}}
      .bridge {{background:#e8daef; border-color:#6c3483;}}
      .audit {{background:#eaecee; border-color:#566573;}}
      .table {{background:#f8f9f9; border-color:#7f8c8d;}}
      .legend {{display:flex; gap:10px; flex-wrap:wrap; margin:8px 0 16px;}}
      .legend span {{padding:5px 10px; border-radius:999px; font-size:12px; border:1px solid #ccc;}}
      .rel-table {{width:100%; border-collapse:collapse; margin-top:10px; font-size:13px;}}
      .rel-table th,.rel-table td {{border:1px solid #ddd; padding:7px; text-align:left;}}
      .rel-table th {{background:#f4f6f7;}}
    </style>
    <div class="legend">
      <span class="fact">Fact</span><span class="dimension">Dimension</span><span class="reference">Reference</span>
      <span class="bridge">Bridge</span><span class="audit">Audit/Lineage</span>
    </div>
    <div class="erd-grid">{''.join(cards)}</div>
    {build_key_mapping_html(model)}
    {rel_table}
    '''



def build_mermaid_html(mermaid_code: str) -> str:
    """Build an embeddable Mermaid HTML page for Streamlit components."""
    safe_mermaid = html.escape(mermaid_code.strip())
    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <script type="module">
        import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
        mermaid.initialize({{
          startOnLoad: true,
          securityLevel: "loose",
          theme: "default",
          er: {{ useMaxWidth: true }},
          flowchart: {{ useMaxWidth: true, htmlLabels: true }}
        }});
      </script>
      <style>
        body {{ margin: 0; font-family: Arial, sans-serif; background: #ffffff; }}
        .mermaid-wrapper {{
          width: 100%;
          min-height: 640px;
          overflow: auto;
          padding: 16px;
          box-sizing: border-box;
          border: 1px solid #e5e7eb;
          border-radius: 12px;
        }}
        .mermaid {{ min-width: 900px; }}
      </style>
    </head>
    <body>
      <div class="mermaid-wrapper">
        <pre class="mermaid">{safe_mermaid}</pre>
      </div>
    </body>
    </html>
    """


def render_mermaid_diagram(mermaid_code: str, height: int = 720) -> None:
    """Render Mermaid diagram inside Streamlit without Graphviz/dot."""
    st.components.v1.html(build_mermaid_html(mermaid_code), height=height, scrolling=True)

def render_erd_panel(model: dict[str, Any] | None, mermaid: str = "", panel_key: str = "main") -> None:
    st.subheader("Generated ERD")

    if mermaid:
        st.markdown("#### Mermaid Diagram")
        render_mermaid_diagram(mermaid, height=720)
        st.download_button(
            "Download Mermaid Code",
            data=mermaid,
            file_name="gold_layer_erd.mmd",
            mime="text/plain",
            use_container_width=True,
            key=f"download_mermaid_{panel_key}",
        )
        with st.expander("View Mermaid Code"):
            st.code(mermaid, language="mermaid")

    if model:
        st.markdown("#### Streamlit Card ERD")
        erd_html = build_erd_html(model)
        st.components.v1.html(erd_html, height=720, scrolling=True)
        st.download_button(
            "Download ERD HTML",
            data=erd_html,
            file_name="gold_layer_erd.html",
            mime="text/html",
            use_container_width=True,
            key=f"download_erd_html_{panel_key}",
        )
        st.download_button(
            "Download ERD JSON",
            data=json.dumps(model, indent=2),
            file_name="gold_layer_erd.json",
            mime="application/json",
            use_container_width=True,
            key=f"download_erd_json_{panel_key}",
        )
        st.download_button(
            "Download ERD Report PDF",
            data=text_to_simple_pdf_bytes("Gold Layer ERD Report", erd_html),
            file_name="gold_layer_erd_report.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"download_erd_pdf_{panel_key}",
        )

    if not model and not mermaid:
        st.info("No ERD found yet. Ask the agent to return Mermaid code or JSON with `tables` and `relationships`.")




# ── Multi-agent orchestration helpers ───────────────────────────────────────
AGENT_PIPELINE = [
    ("Root Orchestrator Agent", "gold_layer_multi_agent_orchestrator"),
    ("Input Validation Agent", "input_validation_agent"),
    ("Silver Schema Analyst Agent", "silver_schema_analyst_agent"),
    ("Data Profiling Agent", "data_profiling_agent"),
    ("Silver Core Agent", "silver_core_agent"),
    ("Gold Validation Agent", "gold_validation_agent"),
    ("Deployment Agent", "deployment_agent"),
    ("Artifact Generation Agent", "artifact_generation_agent"),
]

TOOL_TO_AGENT = {
    "validate_bigquery_access": "Silver Schema Analyst Agent",
    "list_silver_tables": "Silver Schema Analyst Agent",
    "analyze_silver_schema": "Silver Schema Analyst Agent",
    "sample_silver_data": "Data Profiling Agent",
    "generate_gold_design": "Silver Core Agent",
    "create_gold_layer": "Deployment Agent",
    "create_gcs_config": "Deployment Agent",
}


def agent_for_tool(tool_name: str) -> str:
    return TOOL_TO_AGENT.get(tool_name, "Root Orchestrator Agent")


def render_orchestration_panel() -> None:
    st.subheader("Multi-Agent Orchestration")
    st.caption("This UI is synced with the backend `gold_layer_multi_agent_orchestrator` agent.")

    rows = []
    trace_text = "\n".join(st.session_state.get("agent_trace", []))
    for display_name, internal_name in AGENT_PIPELINE:
        status = "Waiting"
        if display_name in trace_text:
            status = "Running / Completed"
        rows.append(
            f"<tr><td><b>{html.escape(display_name)}</b></td>"
            f"<td><code>{html.escape(internal_name)}</code></td>"
            f"<td>{html.escape(status)}</td></tr>"
        )

    st.markdown(
        f"""
        <table class="rel-table">
          <thead><tr><th>Agent</th><th>Backend Name</th><th>Status</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.get("agent_trace"):
        st.markdown("#### Latest Trace")
        st.info("\n\n".join(st.session_state.agent_trace[-20:]))
    else:
        st.info("No orchestration trace yet. Start a chat request to see agent/tool activity.")

# ── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🛠️ Multi-Agent Data Model Development")
    st.caption("Orchestrates specialist agents to design Gold-layer BigQuery datasets and render ERD diagrams.")
    st.divider()
    st.text_input("Agent service URL", value=adk_client.AGENT_SERVICE_URL, disabled=True)
    if FRONTEND_PUBLIC_URL:
        st.link_button("Open Front-End URL", FRONTEND_PUBLIC_URL, use_container_width=True)
        st.caption(FRONTEND_PUBLIC_URL)
    else:
        st.caption("FRONTEND_PUBLIC_URL will show here after deployment.")
    st.text_input("User ID", value=st.session_state.user_id, disabled=True)
    st.text_input("Session ID", value=st.session_state.session_id, disabled=True)
    st.divider()
    if st.button("🔄 New conversation", use_container_width=True, key="new_conversation_btn"):
        reset_conversation()
        st.rerun()
    st.divider()
    with st.expander("What this agent needs from you"):
        st.markdown(
            "- **project_id** — GCP project\n"
            "- **silver_dataset_id** — source Silver layer dataset\n"
            "- **gcs_bucket_name** — config/SQL/DQ output bucket\n"
            "- *(optional)* gold_dataset_id, location"
        )

st.title("Gold Layer Multi-Agent Data Engineer")
st.caption("Chat with the orchestrator. ERD is rendered in Streamlit using colored HTML cards, not Graphviz.")

# ── Ensure backend session exists ───────────────────────────────────────────
if not st.session_state.session_ready:
    try:
        adk_client.get_or_create_session(st.session_state.user_id, st.session_state.session_id)
        st.session_state.session_ready = True
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't reach the agent service at {adk_client.AGENT_SERVICE_URL}: {e}")
        st.stop()

chat_tab, orchestration_tab, erd_tab, paste_tab = st.tabs(["Agent Chat", "Orchestration", "ERD", "Paste JSON"])

with chat_tab:
    st.markdown("#### Conversation")
    chat_box = st.container(height=620)
    with chat_box:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["text"])

    chat_export_text = "\n\n".join(
        f"{m['role'].upper()}: {m['text']}" for m in st.session_state.messages
    ) or "No chat messages yet."
    st.download_button(
        "Download Agent Chat as PDF",
        data=text_to_simple_pdf_bytes("Gold Layer Agent Chat", chat_export_text),
        file_name="gold_layer_agent_chat.pdf",
        mime="application/pdf",
        use_container_width=True,
        key="download_agent_chat_pdf",
    )

with orchestration_tab:
    render_orchestration_panel()

with erd_tab:
    render_erd_panel(st.session_state.latest_erd_model, st.session_state.latest_mermaid_erd, panel_key="erd_tab")

with paste_tab:
    st.subheader("Paste Gold Layer JSON or Mermaid manually")
    pasted_json = st.text_area("Paste JSON with tables and relationships", height=260, key="paste_json_text_area")
    pasted_mermaid = st.text_area("Paste Mermaid code", height=220, key="paste_mermaid_text_area")
    uploaded = st.file_uploader("Or upload metadata.json", type=["json"], key="metadata_json_uploader")

    if st.button("Render Mermaid", use_container_width=True, key="render_mermaid_btn"):
        if pasted_mermaid.strip():
            st.session_state.latest_mermaid_erd = pasted_mermaid.strip()
            st.success("Mermaid diagram generated. Open the ERD tab.")
            render_erd_panel(st.session_state.latest_erd_model, pasted_mermaid.strip(), panel_key="paste_mermaid")
        else:
            st.warning("Paste Mermaid code first.")

    if st.button("Generate ERD from JSON", use_container_width=True, key="generate_erd_from_json_btn"):
        raw = ""
        if uploaded is not None:
            raw = uploaded.read().decode("utf-8")
        elif pasted_json.strip():
            raw = pasted_json

        if not raw.strip():
            st.warning("Paste JSON or upload metadata.json first.")
        else:
            try:
                data = json.loads(raw)
                model = normalize_erd_model(data)
                if not model:
                    st.error("Could not find tables/relationships in JSON.")
                else:
                    st.session_state.latest_erd_model = model
                    st.success("ERD generated. Open the ERD tab.")
                    render_erd_panel(model, panel_key="paste_tab")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

# Keep chat input at the bottom of the browser instead of inside the top tab body.
prompt = st.chat_input(
    "Type here: project_id=my-proj, silver_dataset_id=silver_sales, gcs_bucket_name=my-bucket",
    key="agent_chat_input",
)

if prompt:
    st.session_state.messages.append({"role": "user", "text": prompt})
    accumulated_text = ""
    tool_log: list[str] = []

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status_area = st.empty()
        text_area = st.empty()
        try:
            for event in adk_client.stream_message(
                st.session_state.user_id, st.session_state.session_id, prompt
            ):
                content = event.get("content") or {}
                parts = content.get("parts") or []

                for part in parts:
                    if "functionCall" in part:
                        name = part["functionCall"].get("name", "tool")
                        agent_name = agent_for_tool(name)
                        trace_msg = f"🔧 {agent_name} calling `{name}`…"
                        tool_log.append(trace_msg)
                        st.session_state.agent_trace.append(trace_msg)
                        status_area.info("\n\n".join(tool_log))
                    elif "functionResponse" in part:
                        name = part["functionResponse"].get("name", "tool")
                        agent_name = agent_for_tool(name)
                        trace_msg = f"✅ {agent_name} finished `{name}`"
                        tool_log.append(trace_msg)
                        st.session_state.agent_trace.append(trace_msg)
                        status_area.info("\n\n".join(tool_log))
                    elif "text" in part and part["text"]:
                        accumulated_text += part["text"]
                        text_area.markdown(accumulated_text)

            status_area.empty()
            if not accumulated_text:
                accumulated_text = "_(no text response — check the tool log above)_"
                text_area.markdown(accumulated_text)

            model = extract_erd_model_from_text(accumulated_text)
            mermaid = extract_mermaid_erd(accumulated_text)
            if model:
                st.session_state.latest_erd_model = model
                st.success("ERD JSON detected. Open the ERD tab to view the colored diagram.")
            if mermaid:
                st.session_state.latest_mermaid_erd = mermaid
                st.success("Mermaid diagram detected. Open the ERD tab to view the rendered diagram.")

        except adk_client.AgentServiceError as e:
            accumulated_text = f"⚠️ Agent service error: {e}"
            text_area.markdown(accumulated_text)
        except Exception as e:  # noqa: BLE001
            accumulated_text = f"⚠️ Unexpected error talking to the agent service: {e}"
            text_area.markdown(accumulated_text)

    st.session_state.messages.append({"role": "assistant", "text": accumulated_text})
    st.rerun()
