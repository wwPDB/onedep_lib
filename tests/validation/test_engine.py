import gemmi

from onedep_lib.validation import MmCifValidator, ValidationOptions


def test_uniqueness_reports_all_duplicate_rows_and_isolates_blocks(tmp_path):
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
    engine = MmCifValidator.load([], rules_path=rules, options=ValidationOptions(phases=("cross",)))
    doc = gemmi.cif.read_string("data_a\nloop_\n_cat.id\n1\n1\ndata_b\n_cat.id 1\n")
    before = doc.as_string()
    report = engine.validate_document(doc, source="coordinates")
    assert [(i.rule_id, i.location.data_block, i.location.row, i.message) for i in report.issues] == [
        ("unique", "a", 0, "Duplicate 1"),
        ("unique", "a", 1, "Duplicate 1"),
    ]
    assert report.validation is not None
    assert report.validation.complete
    assert report.validation is not None
    assert not report.validation.valid
    assert doc.as_string() == before
    assert report.to_dict()["validation"]["requested_phases"] == ["cross"]


def test_occupancy_groups_alternate_components_but_separates_models():
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    document = gemmi.cif.read_string("""data_x
loop_
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.pdbx_PDB_model_num
_atom_site.occupancy
A 1 CA ALA 1 0.6
A 1 CA GLY 1 0.6
A 1 CA ALA 2 0.6
""")
    report = engine.validate_document(document, source="model")
    issues = [i for i in report.issues if i.rule_id == "atom_site_occupancy"]
    assert [(i.location.row, i.context["total"]) for i in issues] == [(0, 1.2), (1, 1.2)]
    assert "total occupancy of 1.2" in issues[0].message
