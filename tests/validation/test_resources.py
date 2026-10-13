import json
from pathlib import Path

import gemmi
import pytest

from onedep_lib.validation import MmCifValidator, ValidationConfigurationError, ValidationOptions

OPTIONS = ValidationOptions(phases=("cross",))


def test_packaged_bundle_preserves_all_audited_root_identifiers():
    inventory = json.loads(Path("docs/crosschecks/validation-rule-inventory.json").read_text())
    expected = {r["id"] for table in inventory["tables"] if "id" in table["columns"] for r in table["rows"]}
    engine = MmCifValidator.load([], options=OPTIONS)
    assert engine.rules.root_rule_count == 149
    assert {r.id for r in engine.rules.rules} == expected
    assert engine.rules.schema_version == "2"


def test_original_v1_bundle_uses_resource_defaults(tmp_path):
    # Recover v1 only by removing the additive tables from the packaged CIF.
    import importlib.resources

    source = importlib.resources.files("onedep_lib.validation").joinpath("data/cross_checks.cif")
    doc = gemmi.cif.read_string(source.read_text())
    block = doc[0]
    for category in ("_validator_rule_bundle.", "_validator_rule_message.", "_validator_rule_format."):
        block.find_mmcif_category(category).erase()
    path = tmp_path / "v1.cif"
    path.write_text(doc.as_string())
    engine = MmCifValidator.load([], rules_path=path, options=OPTIONS)
    assert engine.rules.root_rule_count == 149
    assert engine.rules.legacy_defaults_used
    assert engine.rules.defaults_digest


@pytest.mark.parametrize(
    "column,value",
    [
        ("operator", "wat"),
        ("severity", "info"),
        ("extra", "new"),
        ("message", "'unknown [{wat}]'"),
    ],
)
def test_compiler_rejects_invalid_root_configuration(tmp_path, column, value):
    data = {
        "id": "bad",
        "source_category": "cat",
        "source_item": "x",
        "target_category": "cat",
        "target_item": "y",
        "message": "'bad'",
        "operator": "<",
        "severity": "error",
        "extra": ".",
    }
    data[column] = value
    path = tmp_path / "bad.cif"
    path.write_text(
        "data_rules\nloop_\n"
        + "\n".join("_validator_rule_pairwise." + c for c in data)
        + "\n"
        + " ".join(data.values())
        + "\n"
    )
    with pytest.raises(ValidationConfigurationError):
        MmCifValidator.load([], rules_path=path, options=OPTIONS)


def test_invalid_options_fail_instead_of_silent_reordering():
    with pytest.raises(ValidationConfigurationError):
        ValidationOptions(phases=("cross", "dictionary"))


def test_empty_supported_rule_family_compiles_as_explicit_empty_scope(tmp_path):
    path = tmp_path / "empty.cif"
    path.write_text("""data_rules
loop_
_validator_rule_uniqueness.id
_validator_rule_uniqueness.category
_validator_rule_uniqueness.severity
_validator_rule_uniqueness.message
""")
    engine = MmCifValidator.load([], rules_path=path, options=OPTIONS)
    assert engine.rules.root_rule_count == 0


def test_rule_save_frames_are_not_silently_ignored(tmp_path):
    path = tmp_path / "framed.cif"
    path.write_text("""data_rules
save_hidden
_validator_rule_uniqueness.id hidden
_validator_rule_uniqueness.category cat
_validator_rule_uniqueness.severity hard
_validator_rule_uniqueness.message bad
save_
""")
    with pytest.raises(ValidationConfigurationError):
        MmCifValidator.load([], rules_path=path, options=OPTIONS)


def test_prepared_options_cannot_be_replaced_after_resource_fingerprinting():
    engine = MmCifValidator.load([], options=OPTIONS)
    with pytest.raises(AttributeError):
        setattr(engine, "options", ValidationOptions(phases=("dictionary",)))  # noqa: B010 - exercise read-only API
