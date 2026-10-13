"""Public-interface regression scenarios from the normative rule semantics."""

from __future__ import annotations

import gemmi
import pytest

from onedep_lib.validation import MmCifValidator, ValidationContext, ValidationOptions


def validate(data, **options):
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",), **options))
    return engine.validate_document(gemmi.cif.read_string("data_x\n" + data), source="x")


def family_issues(report, family):
    return [i for i in report.issues if i.check_type == "cross" and i.context["family"] == family]


def test_pairwise_uses_first_violating_witness_and_whole_token_message():
    report = validate("""_em_3d_reconstruction.resolution 3
loop_
_em_diffraction_stats.high_resolution
2
4
8
""")
    issue = family_issues(report, "pairwise")[0]
    assert issue.related_locations[0].row == 1
    assert (
        issue.message == "3D reconstruction resolution is higher than diffraction-statistics high-resolution limit 4."
    )
    assert "[{" not in issue.message


def test_linked_presence_break_applies_to_each_source_row_independently():
    report = validate("""loop_
_em_3d_fitting_list.id
_em_3d_fitting_list.source_name
_em_3d_fitting_list.type
_em_3d_fitting_list.accession_code
1 ? 'experimental model' 1abc
2 ? 'experimental model' 2abc
""")
    issues = [i for i in family_issues(report, "linked") if i.location.item.endswith(".source_name")]
    assert [(i.location.row, i.message) for i in issues] == [
        (0, "Source name must be provided."),
        (1, "Source name must be provided."),
    ]


@pytest.mark.parametrize(
    "source_key,target_key,expected",
    [("1", "1", True), ("1", "2", False), ("1", "?", True), ("?", "?", True), ("?", "1", False)],
)
def test_linked_singleton_fallback_never_joins_disagreeing_keys(source_key, target_key, expected):
    report = validate(f"""_refine.pdbx_diffrn_id {source_key}
_refine.ls_d_res_high 1
_reflns.pdbx_diffrn_id {target_key}
_reflns.d_resolution_high 2
""")
    issues = [i for i in family_issues(report, "linked") if i.location.item == "_refine.ls_d_res_high"]
    assert bool(issues) is expected
    if issues:
        assert issues[0].context["fallback_used"] is (target_key == "?")


def test_cross_reference_absent_target_is_a_violation():
    report = validate("_entity_poly.entity_id 1\n")
    issues = family_issues(report, "cross_reference")
    assert len(issues) == 3
    assert {i.context["target_item"] for i in issues} == {
        "_entity_src_gen.entity_id",
        "_entity_src_nat.entity_id",
        "_pdbx_entity_src_syn.entity_id",
    }


def test_explicit_empty_context_suppresses_file_method_inference():
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    doc = gemmi.cif.read_string("data_x\n_exptl.method 'X-RAY DIFFRACTION'\n_diffrn.crystal_id 1\n")
    report = engine.validate_document(doc, source="x", context=ValidationContext(experiment_modes=()))
    assert not family_issues(report, "cross_reference")
    assert report.validation is not None
    assert report.validation.resolved_context["0"]["experiment_modes"] == ()


def test_whitespace_empty_is_unfilled_but_quoted_null_is_a_value():
    report = validate("""loop_
_em_start_model.type
_em_start_model.pdb_id
'PDB ENTRY' ' '
'PDB ENTRY' '?'
""")
    issues = family_issues(report, "conditional_required")
    assert [i.location.row for i in issues] == [0]
    assert issues[0].context["value_state"] == "empty"


def test_one_of_is_category_level_across_rows():
    report = validate("""loop_
_refine.id
_refine.ls_R_factor_obs
1 ?
2 0.2
""")
    assert not family_issues(report, "one_of")


def test_calendar_equality_is_valid_but_impossible_date_blocks_comparison():
    valid = validate(
        "_pdbx_database_status.recvd_initial_deposition_date 2024-2-29\n"
        "_pdbx_database_status.date_coordinates 2024-2-29\n"
    )
    assert not family_issues(valid, "date_order")
    invalid = validate(
        "_pdbx_database_status.recvd_initial_deposition_date 2023-2-29\n"
        "_pdbx_database_status.date_coordinates 2023-3-1\n"
    )
    assert any(i.code == "CROSS_INPUT_UNUSABLE" for i in invalid.issues)
    assert invalid.validation is not None
    assert not invalid.validation.complete


@pytest.mark.parametrize(
    "value,protocol,violates",
    [("1e-3", "SINGLE WAVELENGTH", False), ("1.0-2.0", "LAUE", False), ("1.0,", "LAUE", True)],
)
def test_wavelength_grammar_handles_exponent_minus(value, protocol, violates):
    report = validate(f"""_diffrn_source.diffrn_id 1
_diffrn_source.pdbx_wavelength_list '{value}'
_diffrn_radiation.diffrn_id 1
_diffrn_radiation.pdbx_diffrn_protocol '{protocol}'
""")
    issues = [i for i in report.issues if i.rule_id == "diffrn_source_wavelength_vs_protocol"]
    assert bool(issues) is violates


def test_sequence_direct_mapping_accepts_noninteger_author_ids_and_microheterogeneity():
    report = validate(
        """loop_
_entity_poly_seq.entity_id
_entity_poly_seq.num
_entity_poly_seq.mon_id
1 1 ALA
1 1 GLY
loop_
_atom_site.label_entity_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.auth_seq_id
_atom_site.label_comp_id
_atom_site.label_atom_id
A A 1 abc GLY CA
""".replace("A A 1 abc", "1 A 1 abc")
    )
    assert not [i for i in report.issues if i.rule_id == "atom_site_vs_entity_poly_seq"]
    assert report.validation is not None
    assert report.validation.complete


def test_issue_cap_retains_one_terminal_fatal_and_marks_incomplete():
    report = validate("loop_\n_entity.id\n" + "\n".join("1" for _ in range(100)), max_issues=2)
    assert len(report.issues) == 3
    assert report.issues[-1].code == "VALIDATION_LIMIT_REACHED"
    assert report.validation is not None
    assert report.validation.truncated
    assert report.validation is not None
    assert not report.validation.complete
    assert not report.ok


def test_cancelled_validation_cannot_report_success():
    report = validate("_entity.id 1\n", cancelled=lambda: True)
    assert report.issues[-1].code == "VALIDATION_CANCELLED"
    assert not report.ok
    assert report.validation is not None
    assert not report.validation.complete


def test_join_indexes_are_shared_across_rules_and_parse_occurs_once(tmp_path, monkeypatch):
    path = tmp_path / "model.cif"
    path.write_text("""data_x
loop_
_refine.pdbx_diffrn_id
_refine.ls_d_res_high
_refine.ls_R_factor_R_work
_refine.ls_R_factor_R_free
1 2 0.2 0.3
2 2 0.2 0.3
loop_
_reflns.pdbx_diffrn_id
_reflns.d_resolution_high
_reflns.number_obs
1 2 5
2 2 5
""")
    original = gemmi.cif.read_file
    calls = []

    def read_file(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(gemmi.cif, "read_file", read_file)
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    report = engine.validate_path(path)
    assert calls == [str(path)]
    assert report.validation is not None
    assert report.validation.counters["parses"] == 1
    assert report.validation is not None
    builds = report.validation.counters["index_builds"]
    path.write_text(path.read_text().replace("1 2 0.2 0.3", "\n".join(["1 2 0.2 0.3"] * 100)))
    repeated = engine.validate_path(path)
    assert repeated.validation is not None
    assert repeated.validation.counters["index_builds"] == builds


def test_unfilled_dictionary_enum_source_does_not_require_metadata():
    report = validate("_software.name ?\n_software.version 3\n")
    assert not any(i.code == "DICTIONARY_CROSS_ENUM_UNAVAILABLE" for i in report.issues)
    assert report.validation is not None
    assert report.validation.complete


def test_sequence_mapping_budget_stops_before_unbounded_window_scoring():
    report = validate(
        """loop_
_entity_poly_seq.entity_id
_entity_poly_seq.num
_entity_poly_seq.mon_id
1 1 ALA
1 2 GLY
1 3 SER
loop_
_atom_site.label_entity_id
_atom_site.label_asym_id
_atom_site.auth_seq_id
_atom_site.label_comp_id
_atom_site.label_atom_id
1 A 10 ALA CA
1 A 11 GLY CA
""",
        max_alignment_cells=0,
    )
    assert any(i.code == "SEQUENCE_MAPPING_LIMIT_REACHED" for i in report.issues)
    assert report.validation is not None
    assert not report.validation.complete
    assert not [i for i in report.issues if i.rule_id == "atom_site_vs_entity_poly_seq" and i.check_type == "cross"]


def test_message_substitutions_and_related_locations_are_bounded():
    report = validate("_entity_poly.entity_id '" + ("A" * 100) + "'\n", max_value_chars=8)
    issues = family_issues(report, "cross_reference")
    assert len(issues) == 3
    assert "AAAAAAAA…" in issues[0].message
    assert issues[0].context["message_values_truncated"] == ("source_value",)
    assert len(issues[0].context["source_value"]) == 8


def test_multiple_blocks_do_not_share_keys_and_explicit_selection_reports_scope():
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",), blocks=("second",)))
    document = gemmi.cif.read_string("data_first\n_entity_poly.entity_id 1\ndata_second\n_entity.id 1\n")
    report = engine.validate_document(document, source="x")
    assert report.issues == []
    assert report.validation is not None
    assert report.validation.selected_blocks == ("second",)
    assert report.validation is not None
    assert report.validation.excluded_blocks == ("first",)


def test_mixed_category_loop_is_rejected_instead_of_zip_merging():
    report = validate("loop_\n_entity.id\n_struct_asym.id\n1 A\n")
    assert any(i.code == "CIF_TABLE_LAYOUT" for i in report.issues)
    assert report.validation is not None
    assert not report.validation.complete


def test_conditional_regex_is_applied_only_to_present_targets():
    report = validate("""_em_3d_fitting_list.id 1
_em_3d_fitting_list.type 'experimental model'
_em_3d_fitting_list.source_name PDB
_em_3d_fitting_list.accession_code invalid_long_accession
""")
    issues = family_issues(report, "conditional_regex")
    assert len(issues) == 1
    assert issues[0].location.item == "_em_3d_fitting_list.accession_code"
    assert issues[0].severity.value == "error"


def test_conditional_enumeration_uses_the_driver_branch_message():
    report = validate("""_em_3d_fitting_list.type 'experimental model'
_em_3d_fitting_list.source_name Unsupported
""")
    issues = family_issues(report, "conditional_enum")
    assert len(issues) == 1
    assert issues[0].message == 'For type "experimental model", source name must be PDB or Other.'
    assert issues[0].location.item == "_em_3d_fitting_list.source_name"


def test_category_trigger_requires_targets_and_related_trigger_location():
    report = validate("""_refine.pdbx_method_to_determine_struct 'MOLECULAR REPLACEMENT'
_pdbx_initial_refinement_model.type ?
_pdbx_initial_refinement_model.source_name ?
""")
    issues = family_issues(report, "conditional_category_item")
    assert len(issues) == 2
    assert {i.location.item for i in issues} == {
        "_pdbx_initial_refinement_model.type",
        "_pdbx_initial_refinement_model.source_name",
    }
    assert all(i.related_locations[0].item == "_refine.pdbx_method_to_determine_struct" for i in issues)


def test_any_filled_column_triggers_mandatory_fields():
    report = validate("_em_imaging.id 1\n_em_imaging.nominal_defocus_min 1.0\n")
    issues = family_issues(report, "make_mandatory")
    assert len(issues) == 1
    assert issues[0].location.item == "_em_imaging.nominal_defocus_max"
    assert issues[0].message == "No value present for this mandatory item."


@pytest.mark.parametrize(
    "database,accession,valid",
    [
        ("PDB", "1abc", True),
        ("PDB", "pdb_00001abc", True),
        ("PDB", "bad-long-id", False),
        ("EMDB", "EMD-1234", True),
        ("EMDB", "1abc", False),
    ],
)
def test_resource_accession_formats_are_shared_and_normalized(database, accession, valid):
    report = validate(f"_pdbx_database_related.db_name {database}\n_pdbx_database_related.db_id {accession}\n")
    issues = [i for i in report.issues if i.rule_id == "pdbx_database_related_db_id" and i.check_type == "cross"]
    assert bool(issues) is not valid


def test_wrapped_poly_ala_matches_first_predicate_only():
    report = validate("_entity_poly.pdbx_seq_one_letter_code\n;AAAAA\nAAAAA\n;\n")
    issues = [i for i in report.issues if i.rule_id == "entity_poly_pdbx_seq_one_letter_code_poly_ala"]
    assert len(issues) == 1
    assert issues[0].context["branch"] == 1
    assert "composed only of poly-ALA" in issues[0].message


def test_occupancy_normalizes_integer_model_and_label_residue_identity():
    report = validate("""loop_
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.pdbx_PDB_model_num
_atom_site.occupancy
A 01 CA ALA 01 0.6
A 1 CA ALA 1 0.6
""")
    issues = [i for i in report.issues if i.rule_id == "atom_site_occupancy" and i.check_type == "cross"]
    assert [i.location.row for i in issues] == [0, 1]
    assert all(i.context["total"] == 1.2 for i in issues)


@pytest.mark.parametrize("second,expected", [("0.50004", 0), ("0.50006", 2)])
def test_occupancy_uses_resource_threshold_with_four_decimal_rounding(second, expected):
    report = validate(f"""loop_
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.occupancy
A 1 CA ALA 0.5
A 1 CA ALA {second}
""")
    issues = [i for i in report.issues if i.rule_id == "atom_site_occupancy" and i.check_type == "cross"]
    assert len(issues) == expected


def test_changed_resource_loads_new_snapshot_without_mutating_existing_engine(tmp_path):
    from importlib import resources

    path = tmp_path / "rules.cif"
    path.write_bytes(resources.files("onedep_lib.validation").joinpath("data/cross_checks.cif").read_bytes())
    original = MmCifValidator.load([], rules_path=path, options=ValidationOptions(phases=("cross",)))
    document = gemmi.cif.read_string("""data_x
loop_
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.occupancy
A 1 CA ALA 0.5
A 1 CA ALA 0.5
""")
    resource = gemmi.cif.read_file(str(path))
    limits = resource[0].find_mmcif_category("_validator_rule_procedural_limit.")
    for row in limits:
        if row[1] == "over_one":
            row[2] = "0.9"
    path.write_text(resource.as_string())
    updated = MmCifValidator.load([], rules_path=path, options=ValidationOptions(phases=("cross",)))
    old_report = original.validate_document(document, source="old")
    new_report = updated.validate_document(document, source="new")
    assert not [i for i in old_report.issues if i.rule_id == "atom_site_occupancy"]
    assert len([i for i in new_report.issues if i.rule_id == "atom_site_occupancy"]) == 2
    assert original.rules.digest != updated.rules.digest
    assert old_report.validation is not None
    assert new_report.validation is not None
    assert old_report.validation.resource_fingerprint != new_report.validation.resource_fingerprint
