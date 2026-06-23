"""
Gold Layer Multi-Agent Data Engineering Orchestrator
Uses Google ADK with Gemini 2.5 Flash to design and provision Gold-layer datasets.

This version keeps the original single-agent business flow, but splits responsibility
across specialist agents for orchestration experience.

Agent flow:
1. Root Orchestrator Agent
2. Input Validation Agent
3. Silver Schema Analyst Agent
4. Data Profiling Agent
5. Silver Core Agent
6. Gold Validation Agent
7. Artifact Generation Agent
8. Deployment Agent
"""

import logging
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from .tools import (
    validate_bigquery_access,
    list_silver_tables,
    analyze_silver_schema,
    sample_silver_data,
    generate_gold_design,
    create_gold_layer,
    create_gcs_config,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COMMON_CONTEXT = """# Shared Context — Gold Layer Data Engineering

You are part of a multi-agent data engineering system that designs and provisions
BigQuery Gold-layer datasets from existing Silver-layer datasets.

Mandatory inputs:
- project_id
- silver_dataset_id
- gcs_bucket_name

Optional inputs:
- gold_dataset_id, default: gold_layer
- location, default: US

Never invent table names, columns, relationships, primary keys, foreign keys, or
business meanings that are not derivable from Silver metadata or sampled data.

All generated artifacts must be persisted under:
gold_layer_config/<gold_dataset_id>/<design_id>/
"""

INPUT_VALIDATION_PROMPT = COMMON_CONTEXT + """

# Agent Role — Input Validation Agent

You validate request completeness before any BigQuery or GCS work starts.

Responsibilities:
1. Confirm project_id is supplied.
2. Confirm silver_dataset_id is supplied.
3. Confirm gcs_bucket_name is supplied.
4. Apply defaults only when missing:
   - gold_dataset_id = gold_layer
   - location = asia-south1
5. Halt and ask the user for missing mandatory inputs.

Do not call schema, profiling, design, deployment, or GCS tools.
"""

SILVER_SCHEMA_ANALYST_PROMPT = COMMON_CONTEXT + """

# Agent Role — Silver Schema Analyst Agent

You inspect the Silver layer and prepare schema inventory for modeling.

Execute only after required inputs are available.

Allowed workflow:
1. validate_bigquery_access(project_id, silver_dataset_id)
2. list_silver_tables(project_id, silver_dataset_id)
3. analyze_silver_schema() for every Silver table returned by list_silver_tables

Responsibilities:
- Capture table names, columns, data types, nullable flags, and any key-like columns.
- Identify obvious entity candidates and candidate business keys.
- Do not create Gold design.
- Do not provision BigQuery objects.
- Do not write GCS artifacts.

Output:
- silver_schema_inventory
- table_count
- candidate_entities
- candidate_key_columns
- schema_observations
"""

DATA_PROFILING_PROMPT = COMMON_CONTEXT + """

# Agent Role — Data Profiling Agent

You sample Silver data only when schema metadata alone is insufficient.

Allowed tool:
- sample_silver_data()

Rules:
- Sample sparingly.
- Use the smallest row limit that answers the question.
- Sample only tables/columns needed to resolve ambiguity.
- Do not sample every table by default.
- Do not create Gold design.
- Do not provision BigQuery objects.

Output:
- profiling_findings
- candidate_relationship_evidence
- uniqueness_observations
- data_quality_notes
"""

SILVER_CORE_PROMPT = COMMON_CONTEXT + """

# Agent Role — Silver Core Agent

You are the core dimensional modeling agent. You transform Silver schema knowledge
into a Gold-layer Star Schema design.

This agent replaces the earlier "Gold Data Modeler Agent" name.

Responsibilities:
- Identify business processes, transactions, measures, KPIs, dimensions, and hierarchies.
- Prefer Kimball-style Star Schema.
- Use snowflake modeling only when explicitly justified.
- Build business-focused Fact tables.
- Build conformed reusable Dimensions.
- Define Reference, Bridge, Audit, and Aggregation View objects only where justified.
- Define grain for every Fact and Dimension.
- Define PK, FK, surrogate key, and business key strategy.
- Define BigQuery partitioning and clustering strategy.
- Create Mermaid ERD.
- Create machine-readable JSON metadata.

Relationship Safety Rule:
If a PK/FK relationship cannot be confidently inferred from metadata, naming
conventions, or sampled values, mark it as a candidate relationship and document
the evidence. Do not present uncertain joins as confirmed relationships.

Required design call:
- generate_gold_design(project_id, silver_dataset_id, gold_dataset_id, silver_schemas)

Capture the returned design_id.

Do not call create_gold_layer.
Do not call create_gcs_config.
"""

GOLD_VALIDATION_PROMPT = COMMON_CONTEXT + """

# Agent Role — Gold Validation Agent

You validate the Gold design before deployment.

Validation checklist:
- Every Fact table has at least one measure.
- Every Fact table joins to at least one Dimension.
- Every Dimension has a PK.
- Every FK references a valid PK.
- Surrogate key strategy is documented for every table.
- Grain is documented for every Fact and Dimension.
- All join paths are documented in the Relationship section.
- Partitioning and clustering are specified for every Fact table.

If any validation item fails:
- Halt.
- Return failed checks.
- Explain what must be corrected.
- Do not allow deployment.

If all validation items pass:
- Return validation_status = PASSED.
"""

ARTIFACT_GENERATION_PROMPT = COMMON_CONTEXT + """

# Agent Role — Artifact Generation Agent

You generate user-facing and machine-readable deliverables from the validated Gold design.

Required output sections:
1. BigQuery Summary
2. GCS Summary
3. Assumptions
4. Star Schema Justification
5. Key Mapping Matrix
6. PK/FK & Surrogate Key Documentation
7. Relationship Documentation
8. Mermaid ERD
9. Machine-Readable JSON
10. Streamlit Deliverables
11. Final Deliverables Checklist

Streamlit constraints:
- Streamlit only.
- Do not import or use graphviz, dot.pipe(), dot.render(), or graphviz.Digraph().
- ERD must be renderable through HTML/CSS cards.
- Mermaid ERD may be included as metadata output.
- Every Streamlit interactive widget must have explicit unique key=.
- Provide Agent Chat PDF and ERD Report PDF download buttons.

PDF generation may use built-in lightweight code or a lightweight package such as fpdf2.
If app.py or requirements.txt cannot be physically written by available tools, return
their full code content in the response and clearly state that tool-level file
persistence is unavailable.
"""

DEPLOYMENT_PROMPT = COMMON_CONTEXT + """

# Agent Role — Deployment Agent

You provision the validated Gold layer and persist configuration artifacts.

Allowed workflow:
1. Only proceed when Gold Validation Agent reports validation_status = PASSED.
2. create_gold_layer(project_id, gold_dataset_id, location, design_id)
3. create_gcs_config(gcs_bucket_name, project_id, gold_dataset_id, design_id)

Rules:
- Do not call create_gold_layer before validation passes.
- Treat design_id as the single reference for downstream artifact creation.
- Persist generated artifacts under:
  gold_layer_config/<gold_dataset_id>/<design_id>/
"""

ORCHESTRATOR_PROMPT = COMMON_CONTEXT + """

# Root Orchestrator Agent — Gold Layer Multi-Agent Data Engineer

You coordinate specialist agents to complete the same end-to-end flow as the
original single-agent implementation.

Use this exact orchestration order:
1. Input Validation Agent
2. Silver Schema Analyst Agent
3. Data Profiling Agent, only when schema alone is insufficient
4. Silver Core Agent
5. Gold Validation Agent
6. If validation passes, Deployment Agent
7. Artifact Generation Agent

Important:
- Do not skip the existing business flow.
- Do not reorder deployment before validation.
- Do not call create_gold_layer until validation passes.
- If validation fails, stop deployment and return failed checks.
- Maintain deterministic, explicit output.
- Final response must include all required deliverable sections.

Final output order:
1. BigQuery Summary
2. GCS Summary
3. Assumptions
4. Star Schema Justification
5. Key Mapping Matrix
6. PK/FK & Surrogate Key Documentation
7. Relationship Documentation
8. Mermaid ERD
9. Machine-Readable JSON
10. Streamlit Deliverables
11. Final Deliverables Checklist
"""

# ── Specialist Agents ────────────────────────────────────────────────────────

input_validation_agent = Agent(
    name="input_validation_agent",
    model="gemini-2.5-flash",
    description="Validates mandatory user inputs and applies safe defaults.",
    instruction=INPUT_VALIDATION_PROMPT,
)

silver_schema_analyst_agent = Agent(
    name="silver_schema_analyst_agent",
    model="gemini-2.5-flash",
    description="Validates BigQuery access and analyzes Silver-layer schemas.",
    instruction=SILVER_SCHEMA_ANALYST_PROMPT,
    tools=[
        FunctionTool(func=validate_bigquery_access),
        FunctionTool(func=list_silver_tables),
        FunctionTool(func=analyze_silver_schema),
    ],
)

data_profiling_agent = Agent(
    name="data_profiling_agent",
    model="gemini-2.5-flash",
    description="Samples Silver data only when schema metadata is insufficient.",
    instruction=DATA_PROFILING_PROMPT,
    tools=[
        FunctionTool(func=sample_silver_data),
    ],
)

silver_core_agent = Agent(
    name="silver_core_agent",
    model="gemini-2.5-flash",
    description=(
        "Core modeling agent that converts Silver schema understanding into "
        "Gold-layer Star Schema design."
    ),
    instruction=SILVER_CORE_PROMPT,
    tools=[
        FunctionTool(func=generate_gold_design),
    ],
)

gold_validation_agent = Agent(
    name="gold_validation_agent",
    model="gemini-2.5-flash",
    description="Validates Gold-layer design before deployment.",
    instruction=GOLD_VALIDATION_PROMPT,
)

artifact_generation_agent = Agent(
    name="artifact_generation_agent",
    model="gemini-2.5-flash",
    description="Generates ERD, JSON metadata, Streamlit deliverables, and summaries.",
    instruction=ARTIFACT_GENERATION_PROMPT,
)

deployment_agent = Agent(
    name="deployment_agent",
    model="gemini-2.5-flash",
    description="Creates BigQuery Gold layer and GCS configuration after validation.",
    instruction=DEPLOYMENT_PROMPT,
    tools=[
        FunctionTool(func=create_gold_layer),
        FunctionTool(func=create_gcs_config),
    ]
)

# ── Root Orchestrator ────────────────────────────────────────────────────────

root_agent = Agent(
    name="gold_layer_multi_agent_orchestrator",
    model="gemini-2.5-flash",
    description=(
        "Root orchestrator that coordinates specialist agents to design, validate, "
        "provision, and publish Gold-layer BigQuery datasets from Silver-layer data."
    ),
    instruction=ORCHESTRATOR_PROMPT,
    sub_agents=[
        input_validation_agent,
        silver_schema_analyst_agent,
        data_profiling_agent,
        silver_core_agent,
        gold_validation_agent,
        deployment_agent,
        artifact_generation_agent,
    ]
)
