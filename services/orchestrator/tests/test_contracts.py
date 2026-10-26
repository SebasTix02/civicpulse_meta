"""The JSON Schemas in packages/contracts are the contract; the Pydantic models must agree."""

import json
import typing

from civicpulse.config import REPO_ROOT
from civicpulse.models import IntakeStatus, RegionStatus, SpecLLMOutput

SCHEMAS = REPO_ROOT / "packages/contracts/schemas"


def load(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def test_spec_required_keys_match():
    schema = load("spec_llm_output")
    required = {n for n, f in SpecLLMOutput.model_fields.items() if f.is_required()}
    assert required == set(schema["required"])
    assert set(SpecLLMOutput.model_fields) == set(schema["properties"])


def test_status_enums_match():
    assert set(typing.get_args(IntakeStatus)) == set(load("intake_result")["$defs"]["intake_status"]["enum"])
    assert set(typing.get_args(RegionStatus)) == set(load("closure_verification")["$defs"]["region_status"]["enum"])
