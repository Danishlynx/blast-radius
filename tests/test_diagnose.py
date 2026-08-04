"""Table-driven tests for the diagnosis verdict + severity matrix."""

import pytest

from agent.diagnose import classify, score
from agent.models import BlastNode, BlastRadius, ChangeEvent, ColumnChange


def radius_with(*, model_deployed: bool, env: str | None, models: bool = True) -> BlastRadius:
    change = ChangeEvent(source="cli", entity_urn="urn:li:dataset:(p,x,PROD)", change_type="rename")
    nodes = []
    if models:
        nodes.append(
            BlastNode(
                urn="urn:li:mlModel:(mlflow,m,PROD)",
                entity_type="mlModel",
                deployed=model_deployed,
                env=env if model_deployed else None,
            )
        )
        if model_deployed:
            nodes.append(
                BlastNode(
                    urn="urn:li:mlModelDeployment:(mlflow,d,PROD)",
                    entity_type="mlModelDeployment",
                    env=env,
                )
            )
    return BlastRadius(change=change, nodes=nodes)


def change(kind: str) -> ChangeEvent:
    return ChangeEvent(
        source="cli",
        entity_urn="urn:li:dataset:(p,x,PROD)",
        change_type=kind,
        columns=[ColumnChange(before="amount_usd", after="amount")],
    )


@pytest.mark.parametrize(
    ("change_type", "referenced", "expected"),
    [
        ("rename", True, "breaking"),   # the poison migration
        ("drop", True, "breaking"),
        ("rename", False, "cosmetic"),
        ("type_change", True, "semantic"),
        ("type_change", False, "cosmetic"),
        ("add", True, "cosmetic"),
        ("add", False, "cosmetic"),
    ],
)
def test_classify(change_type, referenced, expected):
    verdict, _ = classify(change(change_type), referenced)
    assert verdict == expected


@pytest.mark.parametrize(
    ("verdict", "referenced", "deployed", "env", "models", "expected"),
    [
        # break-it: breaking + referenced + PROD deployment => P0
        ("breaking", True, True, "PROD", True, "P0"),
        # breaking with a non-prod deployment => P1
        ("breaking", True, True, "STAGING", True, "P1"),
        # semantic shift with PROD deployment => P1
        ("semantic", True, True, "PROD", True, "P1"),
        # breaking, models exist but nothing deployed => P2
        ("breaking", True, False, None, True, "P2"),
        ("semantic", True, False, None, True, "P2"),
        # cosmetic control case: additive/unreferenced => P3
        ("cosmetic", False, True, "PROD", True, "P3"),
        # breaking but nothing downstream at all => P3
        ("breaking", True, False, None, False, "P3"),
    ],
)
def test_severity_matrix(verdict, referenced, deployed, env, models, expected):
    radius = radius_with(model_deployed=deployed, env=env, models=models)
    assert score(verdict, referenced, radius) == expected
