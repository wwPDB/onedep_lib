import gemmi
import pytest

from onedep_lib.validation import MmCifValidator, ValidationConfigurationError, ValidationOptions

RULES = """data_rules
loop_
_validator_rule_pairwise.id
_validator_rule_pairwise.source_category
_validator_rule_pairwise.source_item
_validator_rule_pairwise.target_category
_validator_rule_pairwise.target_item
_validator_rule_pairwise.message
_validator_rule_pairwise.operator
_validator_rule_pairwise.severity
_validator_rule_pairwise.extra
left_check left value right value 'left exceeds right' '>' hard .
right_check right value right limit 'right exceeds limit' '>' hard .
"""


def test_category_selection_reuses_all_rules_and_keeps_cross_category_targets(tmp_path):
    rules = tmp_path / "rules.cif"
    rules.write_text(RULES)
    engine = MmCifValidator.load([], rules_path=rules, options=ValidationOptions(phases=("cross",)))
    document = gemmi.cif.read_string("data_model\n_left.value 2\n_right.value 1\n_right.limit 0\n")
    before = document.as_string()

    report = engine.validate_document(document, source="model", categories=["_LEFT."])
    assert [i.rule_id for i in report.issues] == ["left_check"]
    assert engine.rules.root_rule_count == 2
    assert report.validation is not None
    assert report.validation.requested_categories == ("left",)
    assert report.validation.complete
    execution = {r["rule_id"]: r for r in report.validation.rule_execution}
    assert execution["right_check"]["status"] == "excluded"
    assert execution["right_check"]["skip_reasons"] == {"category_not_selected": 1}
    assert execution["right_check"]["rows_examined"] == 0

    other = engine.validate_document(document, source="model", categories=["right"])
    assert [i.rule_id for i in other.issues] == ["right_check"]
    all_checks = engine.validate_document(document, source="model")
    assert [i.rule_id for i in all_checks.issues] == ["left_check", "right_check"]
    assert all_checks.validation is not None
    assert all_checks.validation.requested_categories is None
    assert document.as_string() == before


DICTIONARY = """data_dictionary
_dictionary.title test
_dictionary.version 1
save_child
_category.id child
_category_key.name '_child.id'
save_
save_parent
_category.id parent
_category_key.name '_parent.id'
save_
save_other
_category.id other
_category_key.name '_other.id'
save_
save_child_id
_item.name '_child.id'
_item_type.code int
save_
save_child_parent
_item.name '_child.parent_id'
_item_type.code int
_item_linked.child_name '_child.parent_id'
_item_linked.parent_name '_parent.id'
save_
save_parent_id
_item.name '_parent.id'
_item_type.code int
save_
save_other_id
_item.name '_other.id'
_item_type.code int
save_
"""


def test_dictionary_scope_ignores_unselected_errors_and_checks_selected_parent_links(tmp_path):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    data = "data_model\n_child.id 1\n_child.parent_id 5\n_parent.id 5\n_other.id invalid\n"
    document = gemmi.cif.read_string(data)
    before = document.as_string()

    selected = engine.validate_document(document, source="model", categories=["child"])
    assert selected.ok
    assert not selected.issues
    assert selected.validation is not None
    assert selected.validation.complete
    assert selected.validation.requested_categories == ("child",)
    assert document.as_string() == before

    all_checks = engine.validate_document(document, source="model")
    assert any(i.location.category == "other" for i in all_checks.errors())

    missing_parent = gemmi.cif.read_string(data.replace("_parent.id 5", "_parent.id 6"))
    mismatch = engine.validate_document(missing_parent, source="model", categories=["child"])
    assert any(i.code == "DICTIONARY_FOREIGN_KEY" and i.location.category == "child" for i in mismatch.errors())
    parent_only = engine.validate_document(missing_parent, source="model", categories=["parent"])
    assert parent_only.ok


def test_unknown_category_does_not_report_successful_empty_validation(tmp_path):
    rules = tmp_path / "rules.cif"
    rules.write_text(RULES)
    engine = MmCifValidator.load([], rules_path=rules, options=ValidationOptions(phases=("cross",)))
    report = engine.validate_document(
        gemmi.cif.read_string("data_model\n_left.value 1\n"), source="model", categories=["misspelled"]
    )
    assert any(i.code == "CIF_CATEGORY_SELECTION" for i in report.issues)
    assert report.validation is not None
    assert not report.validation.complete
    assert report.validation.requested_categories == ("misspelled",)


@pytest.mark.parametrize("categories", [None, []])
def test_omitted_or_empty_category_selection_runs_all_checks_from_path(tmp_path, categories):
    rules = tmp_path / "rules.cif"
    rules.write_text(RULES)
    model = tmp_path / "model.cif"
    model.write_text("data_model\n_left.value 2\n_right.value 1\n_right.limit 0\n")
    engine = MmCifValidator.load([], rules_path=rules, options=ValidationOptions(phases=("cross",)))
    report = engine.validate_path(model, categories=categories)
    assert [i.rule_id for i in report.issues] == ["left_check", "right_check"]
    assert report.validation is not None
    assert report.validation.requested_categories is None

    selected = engine.validate_path(model, categories=["LEFT", "_left.", "left"])
    assert [i.rule_id for i in selected.issues] == ["left_check"]
    assert selected.validation is not None
    assert selected.validation.requested_categories == ("left",)
    assert selected.validation.counters["parses"] == 1


@pytest.mark.parametrize("categories", [["left.value"], [""], [1], 1])
def test_invalid_category_arguments_raise_configuration_error(categories):
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    with pytest.raises(ValidationConfigurationError):
        engine.validate_document(
            gemmi.cif.read_string("data_model\n_entry.id TEST\n"), source="model", categories=categories
        )


def test_atom_site_selection_runs_sequence_check_using_unselected_sequence_data():
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    document = gemmi.cif.read_string("""data_model
_entity_poly_seq.entity_id 1
_entity_poly_seq.num 1
_entity_poly_seq.mon_id ALA
_atom_site.label_entity_id 1
_atom_site.label_asym_id A
_atom_site.label_seq_id 1
_atom_site.label_comp_id GLY
_atom_site.label_atom_id CA
_atom_site.occupancy 1
""")
    report = engine.validate_document(document, source="model", categories=["atom_site"])
    assert any(i.rule_id == "atom_site_vs_entity_poly_seq" for i in report.issues)
    assert any(r.id == "atom_site_vs_entity_poly_seq" and r.check_category == "atom_site" for r in engine.rules.rules)
    assert all(r.check_category is not None for r in engine.rules.rules)


def test_scoped_dictionary_backend_uses_prepared_resource_snapshot(tmp_path):
    dictionary = tmp_path / "dictionary.dic"
    original = """data_dictionary
_dictionary.title test
_dictionary.version 1
save_cat
_category.id cat
_category_key.name '_cat.id'
save_
save_cat_id
_item.name '_cat.id'
_item_type.code int
loop_
_item_enumeration.value
1
2
save_
"""
    dictionary.write_text(original)
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    dictionary.write_text(original.replace("\n1\n2\n", "\n3\n"))
    report = engine.validate_document(
        gemmi.cif.read_string("data_model\n_cat.id 3\n"), source="model", categories=["cat"]
    )
    assert any("not one of the allowed values" in i.message for i in report.errors())


def test_unselected_parent_ambiguity_is_inventory_metadata_not_a_scoped_finding(tmp_path):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(
        DICTIONARY.replace(
            "_item_linked.child_name '_child.parent_id'\n_item_linked.parent_name '_parent.id'",
            "loop_\n_item_linked.child_name\n_item_linked.parent_name\n"
            "'_child.parent_id' '_parent.id'\n'_child.parent_id' '_other.id'",
        )
    )
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    report = engine.validate_document(
        gemmi.cif.read_string("data_model\n_parent.id 5\n"), source="model", categories=["parent"]
    )
    assert not report.issues
    assert report.validation is not None
    assert report.validation.dictionary_coverage["unresolved_parent_tags"] == ("_child.parent_id",)
