import json

import pytest

pytest.importorskip("gemmi")

from onedep_lib.checks.gemmi_crosscheck import (  # noqa: E402
    DEFAULT_RULES_PATH,
    Context,
    CrossChecker,
    load_rules,
    validate,
)
from onedep_lib.checks.gemmi_crosscheck.__main__ import main  # noqa: E402

DUPLICATE_ENTITY = """data_test
_entry.id TEST
loop_
_entity.id
_entity.type
1 polymer
1 polymer
"""

UNIQUE_ENTITIES = """data_test
_entry.id TEST
loop_
_entity.id
_entity.type
1 polymer
2 water
"""

BEGIN_AFTER_END = """data_test
_entry.id TEST
_pdbx_database_status.entry_id TEST
_pdbx_database_status.status_code REL
_pdbx_database_status.recvd_initial_deposition_date 2019-01-01
_pdbx_database_status.date_begin_deposition 2020-08-10
_pdbx_database_status.date_end_processing 2020-07-01
"""


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def _write(tmp_path, text, name="entry.cif"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_packaged_rule_store_loads(rules):
    assert DEFAULT_RULES_PATH.is_file()
    assert DEFAULT_RULES_PATH.parent.name == "cif"
    assert rules["pairwise"]
    assert rules["uniqueness"]


def test_duplicate_entity_id_reported_per_row(tmp_path, rules):
    findings = validate(_write(tmp_path, DUPLICATE_ENTITY), rules=rules)
    assert [(f.rule_id, f.item, f.severity, f.row) for f in findings] == [
        ("uniq_entity_1", "_entity.id", "error", 1),
        ("uniq_entity_1", "_entity.id", "error", 2),
    ]


def test_unique_entity_ids_pass(tmp_path, rules):
    assert validate(_write(tmp_path, UNIQUE_ENTITIES), rules=rules) == []


def test_date_order(tmp_path, rules):
    findings = validate(_write(tmp_path, BEGIN_AFTER_END), rules=rules)
    assert len(findings) == 1
    assert findings[0].item == "_pdbx_database_status.date_begin_deposition"
    assert findings[0].line == 6
    assert "2020-07-01" in findings[0].message


def test_cross_checker_on_in_memory_block(rules):
    from gemmi import cif

    block = cif.read_string(DUPLICATE_ENTITY)[0]
    findings = CrossChecker(block, rules, Context.empty()).run()
    assert {f.rule_id for f in findings} == {"uniq_entity_1"}


def test_cli_json_and_exit_status(tmp_path, capsys):
    bad = _write(tmp_path, DUPLICATE_ENTITY, "bad.cif")
    good = _write(tmp_path, UNIQUE_ENTITIES, "good.cif")

    assert main([str(good), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {str(good): []}

    assert main([str(bad), str(good), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [f["rule_id"] for f in report[str(bad)]] == ["uniq_entity_1", "uniq_entity_1"]


def test_cli_unreadable_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.cif")]) == 2
    assert "cannot read" in capsys.readouterr().err
