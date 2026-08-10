"""Pydantic data models describing the Gold star-schema proposal.

These models are the contract between the modeler (LLM or heuristic), the
DDL/transformation generators, the ERD builder and the executor. Keeping the
shape validated here means a malformed LLM response is caught early with a
clear error rather than producing broken SQL downstream.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TableType(str, Enum):
    DIMENSION = "dimension"
    FACT = "fact"


class PartitionType(str, Enum):
    NONE = "none"
    TIME = "time"
    INTEGER_RANGE = "integer_range"


class Column(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = "STRING"  # BigQuery Standard SQL type
    description: str | None = None
    nullable: bool = True
    is_primary_key: bool = False
    is_business_key: bool = False
    is_measure: bool = False
    # Lineage from the Silver layer
    source_table: str | None = None
    source_column: str | None = None
    # Optional SQL expression used to derive the column during transformation
    source_expression: str | None = None


class ForeignKey(BaseModel):
    model_config = ConfigDict(extra="ignore")

    column: str  # the surrogate key column on this (fact) table
    references_table: str
    references_column: str
    # Lineage used to resolve the surrogate key during transformation:
    # join the Silver source column to the dimension's business key.
    source_column: str | None = None  # Silver column feeding the join
    references_business_key: str | None = None  # dimension business-key column


class Partitioning(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: PartitionType = PartitionType.NONE
    column: str | None = None
    # For time partitioning: DAY | MONTH | YEAR | HOUR
    granularity: str | None = "DAY"
    # For integer_range partitioning
    range_start: int | None = None
    range_end: int | None = None
    range_interval: int | None = None


class Table(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    table_type: TableType
    description: str | None = None
    rationale: str | None = None
    grain: str | None = None
    source_tables: list[str] = Field(default_factory=list)
    primary_key: str | None = None
    columns: list[Column] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    partitioning: Partitioning = Field(default_factory=Partitioning)
    clustering: list[str] = Field(default_factory=list)
    # Optional transformation SQL body (SELECT ...) the modeler produced.
    load_sql: str | None = None


class Relationship(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str = "many-to-one"


class ExcludedTable(BaseModel):
    """A Silver table the modeler intentionally left out of the Gold design."""

    model_config = ConfigDict(extra="ignore")

    table: str
    reason: str | None = None


class Proposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gold_dataset: str
    source_project: str
    source_dataset: str
    business_domain: str | None = None
    summary: str = ""
    dimensions: list[Table] = Field(default_factory=list)
    facts: list[Table] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    excluded_tables: list[ExcludedTable] = Field(default_factory=list)
    data_quality_assumptions: list[str] = Field(default_factory=list)
    transformation_notes: str | None = None

    @property
    def all_tables(self) -> list[Table]:
        return [*self.dimensions, *self.facts]

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
