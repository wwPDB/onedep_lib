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

RULE_TAGS = ("id", "family", "severity", "scope_category", "procedural_method", "message", "enabled")
FLAG_TAGS = ("param_flag", "join_flag", "row_selection_flag")
CONDITION_TAGS = (
    "rule_id",
    "ordinal",
    "role",
    "item_name",
    "operator",
    "value",
    "compare_item_name",
    "group_operator",
    "quantifier",
    "value_transform",
)
JOIN_TAGS = ("rule_id", "ordinal", "scope_item_names", "partner_item_names", "on_no_match")
SELECTION_TAGS = ("rule_id", "category_id", "selector", "selector_item", "group_by", "exclude_items")


def _loop(category, tags, rows):
    if not rows:
        return ""
    lines = ["loop_", *(f"_{category}.{tag}" for tag in tags)]
    lines += [" ".join("." if v is None else cif.quote(str(v)) for v in row) for row in rows]
    return "\n".join(lines) + "\n"


def _pad(rows, tags):
    return [tuple(r) + (None,) * (len(tags) - len(r)) for r in rows]


def make_store(rules, conditions=(), params=(), joins=(), selections=(), flags=None):
    """
    A rule store as text. rules: (id, family, scope, message); conditions and
    selections are padded with '.'. Flags are derived from the records given
    unless overridden.
    """
    rule_rows = []
    for rule_id, family, scope, message in rules:
        derived = [
            "YES" if any(p[0] == rule_id for p in params) else "NO",
            "YES" if any(j[0] == rule_id for j in joins) else "NO",
            "YES" if any(s[0] == rule_id for s in selections) else "NO",
        ]
        method = message.split(":", 1)[0] if family == "procedural" else None
        rule_rows.append((rule_id, family, "ERROR", scope, method, message, "YES", *(flags or derived)))
    return (
        "data_rules\n"
        + _loop("pdbx_deposition_rule", RULE_TAGS + FLAG_TAGS, rule_rows)
        + _loop("pdbx_deposition_rule_condition", CONDITION_TAGS, _pad(conditions, CONDITION_TAGS))
        + _loop("pdbx_deposition_rule_param", ("rule_id", "name", "value"), params)
        + _loop("pdbx_deposition_rule_join", JOIN_TAGS, joins)
        + _loop("pdbx_deposition_rule_row_selection", SELECTION_TAGS, _pad(selections, SELECTION_TAGS))
    )


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture
def custom(tmp_path):
    def load(*args, **kwargs):
        path = tmp_path / "rules.cif"
        path.write_text(make_store(*args, **kwargs))
        return load_rules(path)

    return load


def _run(text, rules, context=None):
    return CrossChecker(cif.read_string(text)[0], rules, context).run()


def _ids(findings):
    return [(f.rule_id, f.row) for f in findings]


def _write(tmp_path, text, name="entry.cif"):
    path = tmp_path / name
    path.write_text(text)
    return path


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

# -- the rule store and the dictionaries ------------------------------------


def test_packaged_rule_store_loads(rules):
    assert DEFAULT_RULES_PATH.name == "cross_checks_deposition_rules.cif"
    assert len(rules) == 138
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
    assert rules.dictionary.name == "mmcif_pdbx.dic"
    assert rules.extension.name == "mmcif_pdbx_deposition_rules_ext.dic"
    # Named in R136 but not defined in the packaged PDBx/mmCIF dictionary.
    assert rules.warnings == [
        "rule R136: row selection on refine_ls_shell: _refine_ls_shell.pdbx_ordinal is not in mmcif_pdbx.dic"
    ]


def test_rule_parts_are_read(rules):
    r056 = rules.get("R056")
    assert r056.partner_categories == ["reflns"]
    assert r056.joins["reflns"].on_no_match == "SINGLE_ROW_FALLBACK"
    r021 = rules.get("R021")
    assert [c.effective_quantifier for c in r021.when + r021.then] == ["ANY"] * 5 + ["ALL"]
    assert rules.get("R044").then[0].value_transform == "ABS"
    r136 = rules.get("R136").selections["refine_ls_shell"]
    assert r136.selector == "ANY_VALUE"
    assert [n.item for n in r136.exclude_items] == ["pdbx_refine_id", "pdbx_ordinal"]
    assert rules.get("R148").then[0].values == ["pdb_id_dep", "deposition_id"]


def test_store_is_checked_against_the_extension_dictionary(tmp_path):
    text = make_store(
        [("X1", "nonsense", "exptl", "m"), ("X2", "conditional_presence", "exptl", "m")],
        [("X2", 1, "THEN", "_exptl.method", "NOT_EMPTY"), ("X9", 1, "THEN", "_exptl.method", "NOT_EMPTY")],
    ).replace("_pdbx_deposition_rule.enabled", "_pdbx_deposition_rule.enabled\n_pdbx_deposition_rule.colour")
    text = text.replace("YES NO NO NO", "YES red NO NO NO")
    path = _write(tmp_path, text, "rules.cif")
    with pytest.raises(RulesError) as exc:
        load_rules(path)
    problems = "\n".join(exc.value.problems)
    assert "_pdbx_deposition_rule.colour is not defined" in problems
    assert "_pdbx_deposition_rule.family = 'nonsense' is not one of" in problems
    assert "'X9' has no _pdbx_deposition_rule.id" in problems


def test_store_is_checked_against_rule_constraints(custom):
    with pytest.raises(RulesError) as exc:
        custom(
            [
                ("X1", "pairwise_order", "refine_ls_shell", "m"),
                ("X2", "format", "exptl", "m"),
                ("X3", "conditional_presence", "refine_ls_shell", "m"),
            ],
            [
                # A misspelt category, as once in R010.
                ("X1", 1, "THEN", "_refine_lsc_shell.d_res_high", "LE_ITEM", None, "_refine_ls_shell.d_res_low"),
                ("X2", 1, "THEN", "_exptl.method", "MATCHES_TYPE", "no_such_type"),
                ("X3", 1, "THEN", "_refine_ls_shell.d_res_high", "NOT_EMPTY"),
            ],
            params=[("X1", "group_by", "_refine_ls_shell.d_res_high")],
            selections=[("X3", "refine_ls_shell", "MIN")],
            flags=("NO", "NO", "YES"),
        )
    problems = "\n".join(exc.value.problems)
    assert "category refine_lsc_shell is not in mmcif_pdbx.dic" in problems
    assert "rule X1: param_flag is NO but the rule has records" in problems
    assert "item type 'no_such_type' is not defined" in problems
    assert "MIN needs selector_item" in problems


def test_unknown_parameter_is_a_warning(custom):
    rules = custom(
        [("X1", "conditional_presence", "exptl", "m")],
        [("X1", 1, "THEN", "_exptl.method", "NOT_EMPTY")],
        params=[("X1", "colour", "red")],
    )
    assert rules.warnings == ["rule X1: parameter 'colour' is not described in mmcif_pdbx_deposition_rules_ext.dic"]


def test_ordering_needs_numbers_or_dates(custom):
    with pytest.raises(RulesError, match="LE_ITEM needs a numeric or date item"):
        custom(
            [("X1", "pairwise_order", "entity", "m")],
            [("X1", 1, "THEN", "_entity.type", "LE_ITEM", None, "_entity.src_method")],
        )


# -- packaged rules -------------------------------------------------------------


def test_duplicate_entity_id_reported_per_row(tmp_path, rules):
    findings = validate(_write(tmp_path, DUPLICATE_ENTITY), rules=rules)
    assert [(f.rule_id, f.item, f.severity, f.row) for f in findings] == [
        ("R018", "_entity.id", "error", 1),
        ("R018", "_entity.id", "error", 2),
    ]
    assert findings[0].message.startswith("Duplicate _entity.id in this file: 1.")


def test_unique_entity_ids_pass(tmp_path, rules):
    assert validate(_write(tmp_path, UNIQUE_ENTITIES), rules=rules) == []


def test_dates_compared_across_date_types(rules):
    # recvd_initial_deposition_date is yyyy-mm-dd; date_coordinates is yyyy-mm-dd:hh:mm.
    findings = _run(
        """data_t
_pdbx_database_status.recvd_initial_deposition_date 2020-08-10
_pdbx_database_status.date_coordinates 2020-07-01:12:00
_pdbx_database_status.date_begin_deposition 2020-08-10
_pdbx_database_status.date_end_processing 2020-07-01
""",
        rules,
    )
    assert [f.rule_id for f in findings] == ["R014", "R017"]
    assert findings[1].line == 4
    assert "(end processing date: 2020-07-01)" in findings[1].message


def test_joined_comparison(rules):
    findings = _run(
        """data_t
_refine.pdbx_refine_id 'X-RAY DIFFRACTION'
_refine.pdbx_diffrn_id 1
_refine.ls_d_res_high 1.5
_refine.ls_d_res_low 40
_refine.ls_R_factor_R_work 0.2
_reflns.pdbx_diffrn_id 1
_reflns.d_resolution_high 2.0
_reflns.d_resolution_low 40
_reflns.pdbx_CC_half 0.9
""",
        rules,
    )
    assert [f.rule_id for f in findings] == ["R056", "R062"]
    assert findings[0].message.endswith(
        "data high-resolution limit 2.0 (value=1.5, condition < _reflns.d_resolution_high)"
    )


def test_missing_compare_value_satisfies_then(rules):
    text = "data_t\n_refine.ls_R_factor_R_work 0.3\n_refine.ls_R_factor_R_free {}\n"
    assert _run(text.format("?"), rules) == []
    # Quoted '?' is missing too.
    assert _run(text.format("'?'"), rules) == []
    assert [f.rule_id for f in _run(text.format("0.25"), rules)] == ["R055"]


def test_or_group_when(rules):
    findings = _run(
        """data_t
loop_
_em_buffer_component.id
_em_buffer_component.name
_em_buffer_component.concentration
_em_buffer_component.concentration_units
_em_buffer_component.formula
1 Tris ? ? ?
2 ? 10 mM ?
""",
        rules,
    )
    assert _ids(findings) == [("R031", 1), ("R034", 1), ("R037", 2)]


def test_one_of_applies_to_every_row(rules):
    findings = _run(
        """data_t
loop_
_reflns_shell.pdbx_ordinal
_reflns_shell.d_res_high
_reflns_shell.d_res_low
_reflns_shell.pdbx_CC_half
1 3.0 40 0.99
2 2.0 3.0 ?
""",
        rules,
    )
    assert _ids(findings) == [("R144", 2)]


def test_any_value_row_selection(rules):
    # R136 checks rows with a value outside pdbx_refine_id and pdbx_ordinal.
    findings = _run(
        """data_t
loop_
_refine_ls_shell.pdbx_refine_id
_refine_ls_shell.pdbx_ordinal
_refine_ls_shell.d_res_high
_refine_ls_shell.d_res_low
_refine_ls_shell.number_reflns_obs
X 1 ? ? ?
X 2 ? ? 1000
""",
        rules,
    )
    assert _ids(findings) == [("R136", 2)]


def test_unreadable_number_counts_as_missing(rules):
    findings = _run(
        "data_t\n_em_imaging.id 1\n_em_imaging.nominal_defocus_min abc\n_em_imaging.nominal_defocus_max 2000\n", rules
    )
    assert [(f.rule_id, f.item) for f in findings] == [("R134", "_em_imaging.nominal_defocus_min")]


def test_absolute_values(rules):
    findings = _run(
        """data_t
_em_imaging.id 1
_em_imaging.nominal_defocus_min 1000
_em_imaging.nominal_defocus_max 2000
_em_imaging.calibrated_defocus_min -2000
_em_imaging.calibrated_defocus_max -1000
""",
        rules,
    )
    # |min| 2000 > |max| 1000, although -2000 < -1000.
    assert [f.rule_id for f in findings] == ["R044", "R045"]


def test_unless_on_a_partner_category(rules):
    text = """data_t
_refine.pdbx_method_to_determine_struct 'MOLECULAR REPLACEMENT'
_refine.ls_R_factor_R_work 0.2
{}"""
    assert [f.rule_id for f in _run(text.format(""), rules)] == ["R088"]
    model = "_pdbx_initial_refinement_model.id 1\n_pdbx_initial_refinement_model.type 'experimental model'\n"
    model += "_pdbx_initial_refinement_model.source_name PDB\n_pdbx_initial_refinement_model.accession_code 1abc\n"
    assert _run(text.format(model), rules) == []


def test_when_through_a_join(rules):
    text = """data_t
loop_
_entity.id
_entity.src_method
1 man
2 nat
loop_
_entity_poly.entity_id
1
2
loop_
_entity_src_gen.entity_id
1
loop_
_entity_src_nat.entity_id
{}
"""
    # Entity 2 is natural but missing from _entity_src_nat (which has entity 3 only).
    assert _ids(_run(text.format("3"), rules)) == [("R105", 2)]
    assert _run(text.format("2"), rules) == []


def test_enumeration_details(rules):
    text = "data_t\n_software.name PHENIX\n_software.classification '{}'\n_software.version 1.20\n"
    # _software.classification is uline, so case does not matter.
    assert _run(text.format("REFINEMENT"), rules) == []
    findings = _run(text.format("data collection"), rules)
    assert [(f.rule_id, f.item) for f in findings] == [("R103", "_software.classification")]
    assert "allowed: refinement, phasing, model building" in findings[0].message


def test_matches_type(rules):
    text = "data_t\n_pdbx_database_related.db_name PDB\n_pdbx_database_related.db_id {}\n"
    for valid in ("1abc", "pdb_00001abc", "D_1000123456"):
        assert _run(text.format(valid), rules) == [], valid
    findings = _run(text.format("abcd"), rules)
    assert [f.rule_id for f in findings] == ["R148"]
    assert findings[0].message.endswith("(value=abcd; expected type: pdb_id_dep, deposition_id)")


def test_occupancy_sum_and_low_value(rules):
    findings = _run(
        """data_t
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
""",
        rules,
    )
    total = "Chain A residue 1 ALA atom CA in model 1 has a total occupancy of 1.1."
    assert [(f.rule_id, f.severity, f.row, f.message) for f in findings] == [
        ("R157", "error", 1, total),
        ("R157", "error", 2, total),
        ("R158", "warning", 3, "Chain A residue 1 ALA atom CB in model 1 has occupancy 0.05 (below 0.1)."),
    ]


def test_poly_ala_in_wrapped_sequence(rules):
    findings = _run(
        """data_t
loop_
_entity_poly.entity_id
_entity_poly.pdbx_seq_one_letter_code
1
;MKTAAAAA
AAAAAG
;
2 AAAA
""",
        rules,
    )
    assert _ids(findings) == [("R160", 2), ("R161", 1)]


def test_wavelength_list_against_protocol(rules):
    findings = _run(
        """data_t
loop_
_diffrn_source.diffrn_id
_diffrn_source.pdbx_wavelength_list
1 ?
2 1.0
3 0.9-1.1
loop_
_diffrn_radiation.diffrn_id
_diffrn_radiation.pdbx_diffrn_protocol
1 LAUE
2 LAUE
3 'SINGLE WAVELENGTH'
""",
        rules,
    )
    assert _ids(findings) == [("R145", 1), ("R146", 2), ("R147", 3)]


def test_cross_reference_and_requested_codes(rules):
    text = "data_t\n{}_em_experiment.id 1\n_em_specimen.id 1\n_em_specimen.experiment_id 2\n"
    # R124 applies when _exptl.method says the entry is EM.
    assert _run(text.format(""), rules) == []
    findings = _run(text.format("_exptl.method 'ELECTRON MICROSCOPY'\n"), rules)
    assert [f.rule_id for f in findings] == ["R124"]
    assert findings[0].message.endswith("(value=2)")
    # R117 applies only when a PDB code is requested.
    assembly = "data_t\n_pdbx_struct_assembly_depositor_info.id 1\n"
    assembly += "_pdbx_struct_assembly_auth_evidence_depositor_info.assembly_id 2\n"
    assert _run(assembly, rules) == []
    assert [f.rule_id for f in _run(assembly, rules, Context({"PDB"}))] == ["R117"]


# -- evaluation features not used by the packaged rules -------------------------

SHELLS = """data_t
_refine.pdbx_refine_id X
_refine.ls_d_res_high 2.0
_refine.ls_R_factor_R_free 0.25
loop_
_refine_ls_shell.pdbx_refine_id
_refine_ls_shell.d_res_high
_refine_ls_shell.d_res_low
_refine_ls_shell.R_factor_R_free
X 3.0 40 0.20
X 2.5 3.0 0.24
X 2.0 2.5 0.30
Y 1.5 2.0 0.40
"""


def test_row_selection_on_scope(custom):
    rules = custom(
        [("X1", "conditional_value", "refine_ls_shell", "Highest-resolution shell R_free too high")],
        [("X1", 1, "THEN", "_refine_ls_shell.R_factor_R_free", "LE", 0.35)],
        selections=[("X1", "refine_ls_shell", "MIN", "_refine_ls_shell.d_res_high", "_refine_ls_shell.pdbx_refine_id")],
    )
    # Only the highest-resolution shell of each refinement (rows 3 and 4) is checked.
    assert _ids(_run(SHELLS, rules)) == [("X1", 4)]


def test_row_selection_on_partner_with_join(custom):
    rules = custom(
        [("X1", "pairwise_order", "refine", "Overall R_free above outer-shell R_free")],
        [("X1", 1, "THEN", "_refine.ls_R_factor_R_free", "GE_ITEM", None, "_refine_ls_shell.R_factor_R_free")],
        joins=[("X1", 1, "_refine.pdbx_refine_id", "_refine_ls_shell.pdbx_refine_id", "SKIP")],
        selections=[("X1", "refine_ls_shell", "MAX", "_refine_ls_shell.d_res_high", "_refine_ls_shell.pdbx_refine_id")],
    )
    # Paired with the lowest-resolution X shell only (R_free 0.20 <= 0.25): no finding.
    assert _run(SHELLS, rules) == []


def test_then_quantifier(custom):
    def rules_with(quantifier):
        return custom(
            [("X1", "pairwise_order", "refine", "Overall R_free below a shell R_free")],
            [
                (
                    "X1",
                    1,
                    "THEN",
                    "_refine.ls_R_factor_R_free",
                    "LE_ITEM",
                    None,
                    "_refine_ls_shell.R_factor_R_free",
                    None,
                    quantifier,
                )
            ],
        )

    # Without a join every shell is a partner; THEN defaults to ALL.
    assert _ids(_run(SHELLS, rules_with(None))) == [("X1", None)]
    assert _run(SHELLS, rules_with("ANY")) == []


def test_then_on_a_partner_item(custom):
    rules = custom(
        [("X1", "conditional_presence", "refine", "Every shell needs an R_free")],
        [("X1", 1, "THEN", "_refine_ls_shell.R_factor_R_free", "NOT_EMPTY")],
    )
    text = SHELLS.replace("X 2.5 3.0 0.24", "X 2.5 3.0 ?")
    findings = _run(text, rules)
    # Reported at the shell that failed (ALL over the partner rows).
    assert [(f.rule_id, f.item, f.row) for f in findings] == [("X1", "_refine_ls_shell.R_factor_R_free", 2)]


def test_existence_over_no_partner_rows(custom):
    def rules_with(quantifier):
        return custom(
            [("X1", "conditional_presence", "refine", "Shells required")],
            [("X1", 1, "THEN", "_refine_ls_shell.d_res_high", "NOT_EMPTY", None, None, None, quantifier)],
        )

    no_shells = "data_t\n_refine.pdbx_refine_id X\n"
    # ANY over no rows does not hold; ALL over no rows holds.
    assert _ids(_run(no_shells, rules_with("ANY"))) == [("X1", None)]
    assert _run(no_shells, rules_with("ALL")) == []


def test_on_no_match(custom):
    def rules_with(on_no_match):
        return custom(
            [("X1", "pairwise_order", "refine_ls_shell", "Shell finer than refinement")],
            [("X1", 1, "THEN", "_refine_ls_shell.d_res_high", "GE_ITEM", None, "_refine.ls_d_res_high")],
            joins=[("X1", 1, "_refine_ls_shell.pdbx_refine_id", "_refine.pdbx_refine_id", on_no_match)],
        )

    assert _run(SHELLS, rules_with("SKIP")) == []
    findings = _run(SHELLS, rules_with("FAIL"))
    assert _ids(findings) == [("X1", 4)]
    assert findings[0].message.endswith("(no matching refine row)")


def test_composite_join(custom):
    rules = custom(
        [("X1", "conditional_presence", "struct_ref_seq", "Alignment without a matching chain")],
        [
            ("X1", 1, "WHEN", "_struct_ref_seq_dif.details", "EQ", "conflict"),
            ("X1", 2, "THEN", "_struct_ref_seq.db_align_beg", "NOT_EMPTY"),
        ],
        joins=[
            (
                "X1",
                1,
                "_struct_ref_seq.align_id,_struct_ref_seq.pdbx_strand_id",
                "_struct_ref_seq_dif.align_id,_struct_ref_seq_dif.pdbx_pdb_strand_id",
                "SKIP",
            )
        ],
    )
    text = """data_t
loop_
_struct_ref_seq.align_id
_struct_ref_seq.pdbx_strand_id
_struct_ref_seq.db_align_beg
1 A ?
1 B ?
loop_
_struct_ref_seq_dif.align_id
_struct_ref_seq_dif.pdbx_pdb_strand_id
_struct_ref_seq_dif.details
1 B conflict
"""
    assert _ids(_run(text, rules)) == [("X1", 2)]


# -- command line -----------------------------------------------------------------


def test_cli_json_and_exit_status(tmp_path, capsys):
    bad = _write(tmp_path, DUPLICATE_ENTITY, "bad.cif")
    good = _write(tmp_path, UNIQUE_ENTITIES, "good.cif")

    assert main([str(good), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {str(good): []}

    assert main([str(bad), str(good), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [f["rule_id"] for f in report[str(bad)]] == ["R018", "R018"]


def test_cli_rule_warnings(tmp_path, capsys):
    good = _write(tmp_path, UNIQUE_ENTITIES)
    assert main([str(good), "--rule-warnings"]) == 0
    assert "_refine_ls_shell.pdbx_ordinal is not in mmcif_pdbx.dic" in capsys.readouterr().err


def test_cli_unreadable_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.cif")]) == 2
    assert "cannot read" in capsys.readouterr().err
