"""Data Modeler Agent — Streamlit UI.

Flow:
  1. User enters project / Silver dataset / Gold dataset / bucket (+ options).
  2. "Analyze Silver Dataset" -> inspect Silver, design star schema, build
     artifacts, upload the *proposal* to GCS. Nothing is created in BigQuery.
  3. User reviews the proposal, ERD and SQL.
  4. "Approve and Create Gold Dataset" -> create dataset + tables, load data,
     upload execution logs, show status.
"""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from src import erd, report
from src.analyzer import analyze
from src.bigquery_client import BigQueryClient
from src.config import get_settings
from src.executor import execute
from src.gcs_storage import GCSStorage, make_run_prefix
from src.logging_config import configure_logging, get_buffer, run_banner
from src.modeler import build_modeler
from src.models import Proposal
from src.validation import validate_inputs

st.set_page_config(page_title="Data Modeler Agent", page_icon="⭐", layout="wide")

settings = get_settings()
log_buffer = configure_logging(settings.log_level)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def _init_state() -> None:
    defaults = {
        "metadata": None,
        "proposal_dict": None,
        "artifacts": None,
        "proposal_uris": None,
        "run_prefix": None,
        "execution": None,
        "inputs": {},
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


_init_state()


def _proposal() -> Proposal | None:
    if st.session_state.proposal_dict is None:
        return None
    return Proposal.model_validate(st.session_state.proposal_dict)


# ---------------------------------------------------------------------------
# Sidebar — inputs
# ---------------------------------------------------------------------------
st.sidebar.title("⭐ Data Modeler Agent")
st.sidebar.caption(
    "Analyze a Silver dataset and propose a Gold-layer star schema. "
    "Nothing is created in BigQuery until you approve."
)

engine = settings.engine_label
st.sidebar.info(f"**Modeling engine:** {engine}\n\n**BQ location:** {settings.bq_location}")

with st.sidebar.form("inputs_form"):
    st.subheader("Inputs")
    project = st.text_input("Source GCP project ID *", value=settings.default_project)
    silver_dataset = st.text_input("Source Silver dataset *", value=settings.default_silver)
    gold_dataset = st.text_input("Target Gold dataset *", value=settings.default_gold)
    bucket = st.text_input("GCS bucket for output *", value=settings.default_bucket)
    business_domain = st.text_input("Business domain / purpose (optional)")
    selected_tables_raw = st.text_input(
        "Tables to include (optional, comma-separated)",
        help="Leave blank to analyze all tables in the Silver dataset.",
    )
    analyze_clicked = st.form_submit_button("🔍 Analyze Silver Dataset", type="primary")

st.sidebar.markdown("---")
st.sidebar.caption("Required fields are marked with *")


# ---------------------------------------------------------------------------
# Analyze action
# ---------------------------------------------------------------------------
def run_analysis() -> None:
    errors = validate_inputs(project, silver_dataset, gold_dataset, bucket)
    if errors:
        for e in errors:
            st.error(e)
        return

    selected_tables = [t.strip() for t in selected_tables_raw.split(",") if t.strip()] or None
    st.session_state.inputs = {
        "project": project.strip(),
        "silver_dataset": silver_dataset.strip(),
        "gold_dataset": gold_dataset.strip(),
        "bucket": bucket.strip(),
    }
    # Reset any prior execution state on a fresh analysis.
    st.session_state.execution = None

    log_buffer.clear()
    log = st.session_state  # alias

    try:
        with st.status("Analyzing Silver dataset…", expanded=True) as status:
            import logging

            logging.getLogger("app").info(run_banner("ANALYSIS"))

            st.write("Connecting to BigQuery…")
            bq = BigQueryClient(project.strip(), location=settings.bq_location)
            if not bq.dataset_exists(silver_dataset.strip()):
                status.update(label="Silver dataset not found", state="error")
                st.error(f"Silver dataset '{silver_dataset}' was not found in project '{project}'.")
                return

            st.write("Validating GCS bucket access…")
            gcs = GCSStorage(bucket.strip(), project=project.strip())
            gcs.check_access()

            st.write("Inspecting tables, schemas, samples and constraints…")
            metadata = analyze(
                bq,
                silver_dataset.strip(),
                selected_tables=selected_tables,
                sample_row_limit=settings.sample_row_limit,
                max_tables=settings.max_tables,
            )
            log.metadata = metadata

            st.write(f"Designing star schema with the {engine} engine…")
            modeler = build_modeler(
                model=settings.gemini_model,
                api_key=settings.gemini_api_key,
                use_vertex=settings.use_vertex,
                project=settings.vertex_project or project.strip(),
                location=settings.vertex_location,
            )
            proposal = modeler.design(metadata, gold_dataset.strip(), business_domain.strip() or None)
            log.proposal_dict = proposal.to_json_dict()

            st.write("Building artifacts (proposal, ERD, DDL, transforms, report)…")
            artifacts = report.build_artifacts(project.strip(), proposal)
            log.artifacts = artifacts

            st.write("Uploading proposal artifacts to GCS…")
            prefix = make_run_prefix(gold_dataset.strip())
            log.run_prefix = prefix
            log.proposal_uris = gcs.upload_bundle(f"{prefix}/proposal", artifacts)

            status.update(label="Analysis complete ✅", state="complete")
    except Exception as exc:  # surface any failure clearly
        import logging

        logging.getLogger("app").exception("Analysis failed")
        st.error(f"Analysis failed: {exc}")


if analyze_clicked:
    run_analysis()


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
st.title("Gold Layer Star-Schema Proposal")

proposal = _proposal()

if proposal is None:
    st.info(
        "Enter your source project, Silver dataset, target Gold dataset and a "
        "GCS bucket in the sidebar, then click **Analyze Silver Dataset**."
    )
    with st.expander("How it works"):
        st.markdown(
            "1. The agent inspects your Silver dataset (schemas, row counts, "
            "samples, constraints).\n"
            "2. It proposes a Gold **star schema**: fact & dimension tables, "
            "keys, relationships, partitioning/clustering, column mappings and "
            "transformation SQL.\n"
            "3. Artifacts are written to your GCS bucket for review.\n"
            "4. **Only after you approve**, it creates the Gold dataset, tables "
            "and loads data."
        )
    st.stop()


# --- Summary metrics -------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Gold dataset", proposal.gold_dataset)
c2.metric("Fact tables", len(proposal.facts))
c3.metric("Dimension tables", len(proposal.dimensions))
c4.metric("Relationships", len(proposal.relationships))

st.markdown(proposal.summary or "")
if proposal.business_domain:
    st.caption(f"Business domain: {proposal.business_domain}")

# --- Source coverage: did we use every Silver table? -----------------------
if st.session_state.metadata:
    cov = report.source_coverage(st.session_state.metadata, proposal)
    if cov["unmapped"]:
        with st.container():
            st.warning(
                f"**{len(cov['mapped'])} of {cov['total']} Silver tables** were used "
                f"in the Gold model. {len(cov['unmapped'])} were not mapped:"
            )
            for t in cov["unmapped"]:
                reason = cov["excluded"].get(t)
                if reason:
                    st.markdown(f"- `{t}` — *excluded by design:* {reason}")
                else:
                    st.markdown(f"- `{t}` — ⚠️ not referenced and not explained")
            st.caption(
                "To force these in, add them to the **Tables to include** box and "
                "re-run, or add a business-domain hint describing how they should be used."
            )
    else:
        st.success(f"All {cov['total']} Silver tables are represented in the Gold model.")


tab_overview, tab_erd, tab_tables, tab_sql, tab_artifacts = st.tabs(
    ["📋 Proposal", "🔗 ERD", "🗂 Tables", "💾 SQL", "📦 Artifacts"]
)

# --- Overview / rationale --------------------------------------------------
with tab_overview:
    st.subheader("Why each table exists")
    st.markdown("**Fact tables**")
    for f in proposal.facts:
        with st.expander(f"⭐ {f.name} — {f.grain or ''}"):
            st.write(f.rationale or "")
            st.caption(f"Sources: {', '.join(f.source_tables) or 'n/a'}  |  PK: {f.primary_key}")
    st.markdown("**Dimension tables**")
    for d in proposal.dimensions:
        with st.expander(f"🔷 {d.name} — {d.grain or ''}"):
            st.write(d.rationale or "")
            st.caption(f"Sources: {', '.join(d.source_tables) or 'n/a'}  |  PK: {d.primary_key}")

    st.subheader("Relationships")
    if proposal.relationships:
        st.table(
            [
                {
                    "From": f"{r.from_table}.{r.from_column}",
                    "To": f"{r.to_table}.{r.to_column}",
                    "Cardinality": r.cardinality,
                }
                for r in proposal.relationships
            ]
        )
    else:
        st.write("No relationships defined.")

    st.subheader("Data quality assumptions")
    for a in proposal.data_quality_assumptions:
        st.markdown(f"- {a}")
    if proposal.transformation_notes:
        st.subheader("Transformation notes")
        st.write(proposal.transformation_notes)

# --- ERD -------------------------------------------------------------------
with tab_erd:
    st.subheader("Entity Relationship Diagram")
    st.caption("⭐ fact tables · 🔷 dimension tables · PK / FK / M = primary key / foreign key / measure")
    svg = erd.build_svg(proposal)
    height = min(erd.svg_pixel_height(proposal), 900)
    components.html(erd.render_svg_page(svg), height=height + 20, scrolling=True)
    st.download_button(
        "⬇ Download ERD (SVG)",
        data=svg,
        file_name="erd.svg",
        mime="image/svg+xml",
        key="dl_erd_svg",
    )
    with st.expander("Mermaid source"):
        st.code(erd.build_mermaid(proposal), language="text")

# --- Tables ----------------------------------------------------------------
with tab_tables:
    for t in proposal.all_tables:
        icon = "⭐" if t.table_type.value == "fact" else "🔷"
        st.markdown(f"### {icon} `{t.name}`")
        part = t.partitioning
        meta = []
        if part and part.type.value != "none" and part.column:
            meta.append(f"Partition: {part.type.value} on `{part.column}` ({part.granularity})")
        if t.clustering:
            meta.append(f"Cluster: {', '.join(t.clustering)}")
        if meta:
            st.caption(" | ".join(meta))
        st.dataframe(
            [
                {
                    "column": c.name,
                    "type": c.type,
                    "key": "PK" if c.is_primary_key else ("FK" if any(c.name == fk.column for fk in t.foreign_keys) else ("measure" if c.is_measure else "")),
                    "nullable": c.nullable,
                    "source": f"{c.source_column}" if c.source_column else (c.source_expression or ""),
                }
                for c in t.columns
            ],
            use_container_width=True,
            hide_index=True,
        )

# --- SQL -------------------------------------------------------------------
with tab_sql:
    ddl_tab, transform_tab = st.tabs(["BigQuery DDL", "Transformation SQL"])
    with ddl_tab:
        st.code(st.session_state.artifacts["gold_ddl.sql"], language="sql")
    with transform_tab:
        st.code(st.session_state.artifacts["transformations.sql"], language="sql")

# --- Artifacts -------------------------------------------------------------
with tab_artifacts:
    st.subheader("Generated artifacts")
    st.caption(f"Uploaded to GCS under: `gs://{st.session_state.inputs['bucket']}/{st.session_state.run_prefix}/proposal/`")
    for name, content in st.session_state.artifacts.items():
        col_a, col_b = st.columns([3, 1])
        col_a.write(f"**{name}**")
        col_a.caption(st.session_state.proposal_uris.get(name, ""))
        col_b.download_button(
            "Download",
            data=content,
            file_name=name,
            key=f"dl_{name}",
        )


# ---------------------------------------------------------------------------
# Approval + execution
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("Approval & Execution")
st.warning(
    "Approving will **create the Gold dataset and tables** in "
    f"`{st.session_state.inputs.get('project')}` and **load data** from Silver. "
    "This modifies your BigQuery project."
)

confirm = st.checkbox("I have reviewed the proposal and want to create the Gold dataset.")
approve = st.button("✅ Approve and Create Gold Dataset", type="primary", disabled=not confirm)


def run_execution() -> None:
    inputs = st.session_state.inputs
    prop = _proposal()
    log_buffer.clear()
    try:
        with st.status("Creating Gold dataset and loading data…", expanded=True) as status:
            import logging

            logging.getLogger("app").info(run_banner("EXECUTION"))
            bq = BigQueryClient(inputs["project"], location=settings.bq_location)
            result = execute(bq, prop)
            st.session_state.execution = {
                "success": result.success,
                "dataset_created": result.dataset_created,
                "steps": [(s.label, s.ok, s.detail) for s in result.steps],
            }

            st.write("Uploading execution logs and results to GCS…")
            gcs = GCSStorage(inputs["bucket"], project=inputs["project"])
            exec_summary = "\n".join(
                f"[{'OK ' if ok else 'FAIL'}] {label} — {detail}"
                for label, ok, detail in st.session_state.execution["steps"]
            )
            gcs.upload_bundle(
                f"{st.session_state.run_prefix}/execution",
                {
                    "execution_summary.txt": exec_summary,
                    "execution_log.txt": log_buffer.text(),
                    "result.json": json.dumps(st.session_state.execution, indent=2),
                },
            )
            state = "complete" if result.success else "error"
            status.update(
                label="Execution complete ✅" if result.success else "Execution finished with errors ⚠️",
                state=state,
            )
    except Exception as exc:
        import logging

        logging.getLogger("app").exception("Execution failed")
        st.error(f"Execution failed: {exc}")


if approve:
    run_execution()


# --- Execution results -----------------------------------------------------
execution = st.session_state.execution
if execution:
    if execution["success"]:
        st.success("Gold dataset created and data loaded successfully.")
    else:
        st.error("Execution finished with errors. See the steps below.")
    st.table(
        [
            {"Step": label, "Status": "✅" if ok else "❌", "Detail": detail}
            for label, ok, detail in execution["steps"]
        ]
    )
    with st.expander("Execution logs"):
        st.code(get_buffer().text(), language="text")
