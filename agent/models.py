"""Core data shapes shared across the agent.

All three triggers (poller, kafka, cli) normalize to the same ChangeEvent, so
every downstream stage runs one code path regardless of how a change was
detected. BlastRadius is the traversal output consumed by diagnose/act.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ColumnChange(BaseModel):
    before: str | None = None
    after: str | None = None
    type_before: str | None = None
    type_after: str | None = None


ChangeType = Literal["rename", "drop", "type_change", "add", "other"]


class ChangeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: Literal["poller", "kafka", "cli"]
    entity_urn: str
    change_type: ChangeType
    columns: list[ColumnChange] = []
    raw: dict = {}


class BlastNode(BaseModel):
    urn: str
    entity_type: str  # dataset | mlFeatureTable | mlFeature | mlModel | mlModelDeployment
    name: str = ""
    platform: str = ""
    hop: int = 0
    deployed: bool | None = None  # mlModel only: has a linked deployment
    env: str | None = None  # deployment env (e.g. PROD)
    owners: list[str] = []


class BlastRadius(BaseModel):
    change: ChangeEvent
    nodes: list[BlastNode] = []
    # Each path is a chain of URNs from the changed asset to a leaf.
    paths: list[list[str]] = []
    # column-level when every dataset hop had fine-grained lineage; else table-level.
    confidence: Literal["column-level", "table-level"] = "column-level"

    @property
    def models(self) -> list[BlastNode]:
        return [n for n in self.nodes if n.entity_type == "mlModel"]

    @property
    def deployments(self) -> list[BlastNode]:
        return [n for n in self.nodes if n.entity_type == "mlModelDeployment"]

    @property
    def all_owners(self) -> list[str]:
        seen: dict[str, None] = {}
        for n in self.nodes:
            for o in n.owners:
                seen.setdefault(o, None)
        return list(seen)
