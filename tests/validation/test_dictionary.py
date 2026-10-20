import gemmi
import pytest

from onedep_lib.validation import MmCifValidator, ValidationConfigurationError, ValidationOptions

DICTIONARY = """data_minimal
_dictionary.title minimal
_dictionary.version 1
save_cat
_category.id cat
_category_key.name '_cat.id'
save_
save_cat_id
_item.name '_cat.id'
_item.mandatory_code yes
_item_type.code int
save_
save_cat_value
_item.name '_cat.value'
_item.mandatory_code yes
_item_type.code float
save_
"""


def test_dictionary_rejects_silent_backend_integer_gap_and_preserves_raw_token(tmp_path):
    path = tmp_path / "dictionary.dic"
    path.write_text(DICTIONARY)
    engine = MmCifValidator.load([path], options=ValidationOptions(phases=("dictionary",)))
    report = engine.validate_document(gemmi.cif.read_string("data_x\n_cat.id nope\n_cat.value 1.0\n"), source="model")
    assert not report.ok
    issue = next(i for i in report.issues if i.code == "DICTIONARY_TYPE")
    assert issue.location.item == "_cat.id"
    assert issue.actual == "nope"
    assert report.validation is not None
    assert report.validation.complete
    assert report.validation is not None
    assert not report.validation.valid


def test_dictionary_errors_block_cross_checks_before_domain_findings(tmp_path):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)
    rules = tmp_path / "rules.cif"
    rules.write_text("""data_rules
loop_
_validator_rule_uniqueness.id
_validator_rule_uniqueness.category
_validator_rule_uniqueness.severity
_validator_rule_uniqueness.message
unique cat hard 'Duplicate [{duplicate_display}]'
loop_
_validator_rule_uniqueness_key.rule_id
_validator_rule_uniqueness_key.ordinal
_validator_rule_uniqueness_key.item
unique 1 id
""")
    engine = MmCifValidator.load([dictionary], rules_path=rules)
    report = engine.validate_document(
        gemmi.cif.read_string("data_x\nloop_\n_cat.id\n_cat.value\n1 1\n1 2\n"), source="x"
    )
    assert not any(i.check_type == "cross" for i in report.issues)
    assert report.validation is not None
    assert report.validation.rule_execution[0]["status"] == "blocked"
    assert report.validation is not None
    assert not report.validation.complete


@pytest.mark.parametrize("raw", ["'1'", "1.2", "1e2", "nan", "+", "'١'"])
def test_integer_lexical_policy_rejects_non_ascii_or_quoted_forms(tmp_path, raw):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    report = engine.validate_document(gemmi.cif.read_string(f"data_x\n_cat.id {raw}\n_cat.value 1\n"), source="x")
    assert any(i.code == "DICTIONARY_TYPE" for i in report.issues)


def test_default_validation_requires_real_dictionary_resources():
    with pytest.raises(ValidationConfigurationError, match="Dictionary"):
        MmCifValidator.load([])


def test_composite_parent_tuple_is_checked_with_case_insensitive_tags(tmp_path):
    dictionary = tmp_path / "links.dic"
    dictionary.write_text("""data_links
_dictionary.title links
_dictionary.version 1
loop_
_pdbx_item_linked_group_list.child_category_id
_pdbx_item_linked_group_list.link_group_id
_pdbx_item_linked_group_list.child_name
_pdbx_item_linked_group_list.parent_name
child 1 '_child.a' '_parent.a'
child 1 '_child.b' '_parent.b'
save_child_a
_item.name '_child.a'
_item_type.code int
save_
save_child_b
_item.name '_child.b'
_item_type.code int
save_
save_parent_a
_item.name '_parent.a'
_item_type.code int
save_
save_parent_b
_item.name '_parent.b'
_item_type.code int
save_
""")
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    report = engine.validate_document(
        gemmi.cif.read_string("""data_x
_CHILD.A 1
_CHILD.B 2
loop_
_parent.a
_parent.b
1 3
4 2
"""),
        source="x",
    )
    assert not report.ok
    assert any(i.code == "DICTIONARY_FOREIGN_KEY" and i.context.get("supplement") for i in report.issues)


@pytest.mark.parametrize(
    "name,classification,expected",
    [
        ("Tool", "refinement", None),
        ("Tool", "collection", "Allowed values: refinement."),
        ("Tool", "?", None),
        ("Other", "?", "no dictionary cross-enumeration mapping is defined."),
    ],
)
def test_dictionary_cross_enumeration_uses_details_and_keeps_missing_policy(tmp_path, name, classification, expected):
    dictionary = tmp_path / "software.dic"
    dictionary.write_text("""data_software
_dictionary.title software
_dictionary.version 1
loop_
_item_type_list.code
_item_type_list.primitive_code
_item_type_list.construct
char char '.*'
save_software
_category.id software
save_
save_name
_item.name '_software.name'
_item_type.code char
loop_
_item_enumeration.value
_item_enumeration.detail
Tool refinement
Other .
save_
save_classification
_item.name '_software.classification'
_item_type.code char
save_
""")
    rules = tmp_path / "software.cif"
    rules.write_text("""data_rules
loop_
_validator_rule_dictionary_enum.id
_validator_rule_dictionary_enum.category
_validator_rule_dictionary_enum.source_item
_validator_rule_dictionary_enum.cross_item
software_policy software name classification
""")
    engine = MmCifValidator.load([dictionary], rules_path=rules)
    doc = gemmi.cif.read_string(f"data_x\n_software.name {name}\n_software.classification {classification}\n")
    report = engine.validate_document(doc, source="x")
    issues = [i for i in report.issues if i.check_type == "cross"]
    assert len(issues) == (0 if expected is None else 1)
    if expected is not None:
        assert issues[0].message.endswith(expected)
        assert issues[0].rule_id == "software_policy"


def test_repeated_composite_link_operand_keeps_tuple_validation(tmp_path):
    dictionary = tmp_path / "repeated.dic"
    dictionary.write_text("""data_links
_dictionary.title links
_dictionary.version 1
save_child
_category.id child
save_
save_parent
_category.id parent
save_
loop_
_pdbx_item_linked_group_list.child_category_id
_pdbx_item_linked_group_list.link_group_id
_pdbx_item_linked_group_list.child_name
_pdbx_item_linked_group_list.parent_name
child 1 '_child.a' '_parent.a'
child 1 '_child.a' '_parent.a'
child 1 '_child.b' '_parent.b'
save_child_a
_item.name '_child.a'
_item_type.code int
save_
save_child_b
_item.name '_child.b'
_item_type.code int
save_
save_parent_a
_item.name '_parent.a'
_item_type.code int
save_
save_parent_b
_item.name '_parent.b'
_item_type.code int
save_
""")
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    valid = engine.validate_document(
        gemmi.cif.read_string("data_valid\n_child.a 1\n_child.b 2\n_parent.a 1\n_parent.b 2\n"),
        source="valid",
    )
    assert valid.ok
    invalid = engine.validate_document(
        gemmi.cif.read_string("data_invalid\n_child.a 1\n_child.b 2\n_parent.a 1\n_parent.b 3\n"),
        source="invalid",
    )
    # Every tag here is spelled in its canonical (lowercase) case, so Gemmi's own linked-group
    # checker is trusted for this one (see test_composite_parent_tuple_is_checked_with_case_insensitive_tags
    # for the mixed-case fallback the Python supplement still owns); this still exercises tuple
    # validation surviving a repeated composite-link operand declaration, just via the native path.
    assert any(i.code == "DICTIONARY_FOREIGN_KEY" for i in invalid.issues)


def test_shared_definitions_with_alias_metadata_allow_canonical_cross_checks(tmp_path):
    dictionary = tmp_path / "shared.dic"
    dictionary.write_text("""data_shared
_dictionary.title shared
_dictionary.version 1
save_cat
_category.id cat
save_
save_child
_category.id child
save_
save_cat_x
loop_
_item.name
_item.category_id
'_cat.x' cat
'_child.x' child
_item_type.code int
_item_aliases.alias_name '_legacy.x'
save_
save_cat_y
_item.name '_cat.y'
_item_type.code int
save_
save_child_x
_item.name '_child.x'
_item_description.description 'Child definition with inherited type'
save_
""")
    rules = tmp_path / "rules.cif"
    rules.write_text("""data_rules
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
canonical_cross cat x cat y 'x exceeds y' '>' hard .
""")
    engine = MmCifValidator.load([dictionary], rules_path=rules)
    report = engine.validate_document(
        gemmi.cif.read_string("data_model\n_cat.x 2\n_cat.y 1\n_child.x 2\n"), source="model"
    )
    assert any(i.rule_id == "canonical_cross" for i in report.issues)
    assert report.validation is not None
    assert report.validation.complete
    assert report.validation.dictionary_coverage["aliases_supported"] is False


def test_input_using_historical_alias_is_explicitly_incomplete(tmp_path):
    dictionary = tmp_path / "aliases.dic"
    dictionary.write_text(
        DICTIONARY.replace("_item_type.code int\n", "_item_type.code int\n_item_aliases.alias_name '_legacy.id'\n")
    )
    engine = MmCifValidator.load([dictionary], options=ValidationOptions(phases=("dictionary",)))
    report = engine.validate_document(gemmi.cif.read_string("data_alias\n_legacy.id 1\n"), source="alias")
    assert any(i.code == "DICTIONARY_ALIAS_UNSUPPORTED" for i in report.issues)
    assert report.validation is not None
    assert not report.validation.complete


def test_ambiguous_parent_metadata_does_not_block_unrelated_cross_checks(tmp_path):
    dictionary = tmp_path / "parents.dic"
    dictionary.write_text("""data_parents
_dictionary.title parents
_dictionary.version 1
save_cat
_category.id cat
save_
save_child
_category.id child
save_
save_parent
_category.id parent
save_
save_other
_category.id other
save_
save_cat_x
_item.name '_cat.x'
_item_type.code int
save_
save_cat_y
_item.name '_cat.y'
_item_type.code int
save_
save_child_id
_item.name '_child.id'
_item_type.code int
loop_
_item_linked.child_name
_item_linked.parent_name
'_child.id' '_parent.id'
'_child.id' '_other.id'
save_
save_parent_id
_item.name '_parent.id'
_item_type.code int
save_
save_other_id
_item.name '_other.id'
_item_type.code int
save_
""")
    rules = tmp_path / "rules.cif"
    rules.write_text("""data_rules
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
unrelated cat x cat y 'x exceeds y' '>' hard .
""")
    engine = MmCifValidator.load([dictionary], rules_path=rules)
    report = engine.validate_document(gemmi.cif.read_string("data_model\n_cat.x 2\n_cat.y 1\n"), source="model")
    assert any(i.rule_id == "unrelated" for i in report.issues)
    assert any(i.code == "DICTIONARY_RESOURCE_DIAGNOSTIC" and i.severity.value == "warning" for i in report.issues)
    assert report.validation is not None
    assert report.validation.complete

    affected = engine.validate_document(
        gemmi.cif.read_string("data_affected\n_cat.x 2\n_cat.y 1\n_child.id 1\n_parent.id 1\n_other.id 1\n"),
        source="affected",
    )
    assert any(i.code == "DICTIONARY_PARENT_UNSUPPORTED" for i in affected.issues)
    assert not any(i.rule_id == "unrelated" for i in affected.issues)
    assert affected.validation is not None
    assert not affected.validation.complete


def test_cli_resource_policy_blocks_only_rules_with_missing_dictionary_definitions(tmp_path):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)
    rules = tmp_path / "rules.cif"
    rules.write_text("""data_rules
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
supported cat id cat value 'id exceeds value' '>' hard .
unavailable cat missing cat value 'missing exceeds value' '>' hard .
""")
    with pytest.raises(ValidationConfigurationError, match="incompatible"):
        MmCifValidator.load([dictionary], rules_path=rules)
    engine = MmCifValidator.load(
        [dictionary], rules_path=rules, options=ValidationOptions(rule_dictionary_policy="block_rule")
    )
    report = engine.validate_document(gemmi.cif.read_string("data_model\n_cat.id 2\n_cat.value 1\n"), source="model")
    assert any(i.rule_id == "supported" and i.check_type == "cross" for i in report.issues)
    assert any(i.rule_id == "unavailable" and i.code == "CROSS_DICTIONARY_INCOMPATIBLE" for i in report.issues)
    assert report.validation is not None
    assert not report.validation.complete
    execution = {r["rule_id"]: r for r in report.validation.rule_execution}
    assert execution["unavailable"]["status"] == "blocked"
