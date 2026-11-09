import json

import pytest

pytest.importorskip("gemmi")

from gemmi import cif  # noqa: E402

from onedep_lib.checks.crosscheck import (  # noqa: E402
    DEFAULT_RULES_PATH,
    Context,
    CrossChecker,
    RulesError,
    load_rules,
    validate,
)
from onedep_lib.checks.crosscheck.__main__ import main  # noqa: E402

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

REFINE_VS_REFLNS = """data_test
_refine.pdbx_refine_id 'X-RAY DIFFRACTION'
_refine.pdbx_diffrn_id 1
_refine.ls_d_res_high 1.5
_refine.ls_d_res_low 40
_refine.ls_R_factor_R_work 0.2
_reflns.pdbx_diffrn_id 1
_reflns.d_resolution_high 2.0
_reflns.d_resolution_low 40
_reflns.pdbx_CC_half 0.9
"""

BUFFER = """data_test
loop_
_em_buffer_component.id
_em_buffer_component.name
_em_buffer_component.concentration
_em_buffer_component.concentration_units
_em_buffer_component.formula
1 Tris ? ? ?
2 ? 10 mM ?
"""

OCCUPANCY = """data_test
loop_
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.auth_atom_id
_atom_site.occupancy
1 CA A ALA A 1 CA 0.60
2 CA B ALA A 1 CA 0.50
3 CB . ALA A 1 CB 0.05
"""

POLY_ALA = """data_test
loop_
_entity_poly.entity_id
_entity_poly.pdbx_seq_one_letter_code
1
;MKTAAAAA
AAAAAG
;
2 AAAA
"""

SPECIMEN = """data_test
_em_experiment.id 1
_em_specimen.id 1
_em_specimen.experiment_id 2
"""

WAVELENGTH = """data_test
loop_
_diffrn_source.diffrn_id
_diffrn_source.pdbx_wavelength_list
1 ?
2 1.0
loop_
_diffrn_radiation.diffrn_id
_diffrn_radiation.pdbx_diffrn_protocol
1 LAUE
2 LAUE
"""

RULE_STORE = """data_rules
loop_
_pdbx_deposition_rule.id
_pdbx_deposition_rule.family
_pdbx_deposition_rule.severity
_pdbx_deposition_rule.scope_category
_pdbx_deposition_rule.procedural_method
_pdbx_deposition_rule.message
_pdbx_deposition_rule.enabled
_pdbx_deposition_rule.param_flag
R1 conditional_presence ERROR exptl . 'Crystals required' YES NO
R2 conditional_presence ERROR exptl . 'Disabled' NO NO
loop_
_pdbx_deposition_rule_condition.rule_id
_pdbx_deposition_rule_condition.ordinal
_pdbx_deposition_rule_condition.role
_pdbx_deposition_rule_condition.item_name
_pdbx_deposition_rule_condition.operator
_pdbx_deposition_rule_condition.value
_pdbx_deposition_rule_condition.other_item_name
_pdbx_deposition_rule_condition.group_operator
R1 2 THEN '_exptl.crystals_number' NOT_EMPTY . . .
R1 1 WHEN '_exptl.method' IN 'X-RAY DIFFRACTION, NEUTRON DIFFRACTION' . .
R1 3 UNLESS '_exptl.details' EQ skip . .
R2 1 THEN '_exptl.method' EMPTY . . .
"""


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def _run(text, rules, context=None):
    return CrossChecker(cif.read_string(text)[0], rules, context or Context.empty()).run()


def _write(tmp_path, text, name="entry.cif"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_packaged_rule_store_loads(rules):
    assert DEFAULT_RULES_PATH.name == "cross_checks_deposition_rules.cif"
    assert len(rules) == len({r.id for r in rules}) == 161
    assert {r.family for r in rules} == {
        "aggregate",
        "conditional_presence",
        "conditional_value",
        "cross_reference",
        "format",
        "pairwise_order",
        "procedural",
        "uniqueness",
    }
    assert all(r.then or r.method for r in rules)


def test_rule_store_parsing(tmp_path):
    rules = load_rules(_write(tmp_path, RULE_STORE, "rules.cif"))
    r1 = rules[0]
    assert [c.role for c in r1.when + r1.then + r1.unless] == ["WHEN", "THEN", "UNLESS"]
    assert r1.when[0].values == ["X-RAY DIFFRACTION", "NEUTRON DIFFRACTION"]
    assert not rules[1].enabled

    def ids(text):
        return [f.rule_id for f in _run(text, rules)]

    assert ids("data_t\n_exptl.method 'NEUTRON DIFFRACTION'\n") == ["R1"]
    assert ids("data_t\n_exptl.method 'NEUTRON DIFFRACTION'\n_exptl.details skip\n") == []
    assert ids("data_t\n_exptl.method 'X-RAY DIFFRACTION'\n_exptl.crystals_number 1\n") == []
    assert ids("data_t\n_exptl.method 'ELECTRON MICROSCOPY'\n") == []


def test_rule_store_rejects_unknown_rule(tmp_path):
    bad = RULE_STORE.replace("R2 1 THEN", "R9 1 THEN")
    with pytest.raises(RulesError, match="R9"):
        load_rules(_write(tmp_path, bad, "rules.cif"))


def test_duplicate_entity_id_reported_per_row(tmp_path, rules):
    findings = validate(_write(tmp_path, DUPLICATE_ENTITY), rules=rules)
    assert [(f.rule_id, f.item, f.severity, f.row) for f in findings] == [
        ("uniq_entity_1", "_entity.id", "error", 1),
        ("uniq_entity_1", "_entity.id", "error", 2),
    ]
    assert findings[0].message.startswith("Duplicate _entity.id in this file: 1.")


def test_unique_entity_ids_pass(tmp_path, rules):
    assert validate(_write(tmp_path, UNIQUE_ENTITIES), rules=rules) == []


def test_date_order(rules):
    findings = _run(BEGIN_AFTER_END, rules)
    assert len(findings) == 1
    assert findings[0].item == "_pdbx_database_status.date_begin_deposition"
    assert findings[0].line == 6
    assert findings[0].message.endswith("(end processing date: 2020-07-01).")


def test_joined_comparison(rules):
    findings = {f.rule_id: f for f in _run(REFINE_VS_REFLNS, rules)}
    assert set(findings) == {"linked_refine_ls_d_res_high_1", "linked_reflns_d_resolution_high_1"}
    assert findings["linked_refine_ls_d_res_high_1"].message.endswith("data high-resolution limit 2.0")


def test_linked_presence_reports_once_per_item_with_break_flag(rules):
    findings = [(f.rule_id, f.row) for f in _run(BUFFER, rules)]
    assert findings == [
        ("linked_em_buffer_component_concentration_1", 1),
        ("linked_em_buffer_component_concentration_units_1", 1),
        ("linked_em_buffer_component_name_1", 2),
    ]


def test_occupancy_sum_and_low_value(rules):
    findings = [(f.rule_id, f.severity, f.row, f.message) for f in _run(OCCUPANCY, rules)]
    total = "Chain A residue 1 ALA atom CA in model 1 has a total occupancy of 1.1."
    assert findings == [
        ("atom_site_occupancy_over_one", "error", 1, total),
        ("atom_site_occupancy_over_one", "error", 2, total),
        (
            "atom_site_occupancy_below",
            "warning",
            3,
            "Chain A residue 1 ALA atom CB in model 1 has occupancy 0.05 (below 0.1).",
        ),
    ]


def test_poly_ala_in_wrapped_sequence(rules):
    findings = [(f.rule_id, f.row) for f in _run(POLY_ALA, rules)]
    assert findings == [
        ("entity_poly_pdbx_seq_one_letter_code_poly_ala_b1", 2),
        ("entity_poly_pdbx_seq_one_letter_code_poly_ala_b2", 1),
    ]


def test_wavelength_list_against_protocol(rules):
    # A missing list counts as empty, so LAUE without one is an error, as is LAUE with a single wavelength.
    findings = [(f.rule_id, f.row) for f in _run(WAVELENGTH, rules)]
    assert findings == [
        ("diffrn_source_wavelength_vs_protocol_empty", 1),
        ("diffrn_source_wavelength_vs_protocol_b1", 2),
    ]


def test_cross_reference_gated_on_experiment(rules):
    assert _run(SPECIMEN, rules) == []
    findings = _run(SPECIMEN, rules, Context({"em"}, set(), set()))
    assert [f.rule_id for f in findings] == ["xref_em_specimen_em_experiment_21"]
    assert findings[0].message.endswith("(value=2)")


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
