from pathlib import Path

import pytest

from onedep_lib.validation import MmCifValidator, ValidationOptions

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "name,rule_id,expected",
    [
        ("test_procedural_occupancy_invalid_total_over_one.cif", "atom_site_occupancy", 3),
        ("test_procedural_occupancy_valid_altlocs_sum_to_one.cif", "atom_site_occupancy", 0),
        ("test_procedural_occupancy_warning_below_point_one.cif", "atom_site_occupancy", 1),
        ("test_procedural_occupancy_edge_exactly_point_one.cif", "atom_site_occupancy", 0),
        ("test_procedural_occupancy_edge_exactly_one.cif", "atom_site_occupancy", 0),
        ("test_procedural_occupancy_edge_missing.cif", "atom_site_occupancy", 0),
        ("test_procedural_sequence_mismatch_invalid.cif", "atom_site_vs_entity_poly_seq", 1),
        ("test_procedural_sequence_mismatch_valid.cif", "atom_site_vs_entity_poly_seq", 0),
        ("test_procedural_sequence_mismatch_unmodelled_ok.cif", "atom_site_vs_entity_poly_seq", 0),
        ("test_procedural_sequence_mismatch_no_label_seq.cif", "atom_site_vs_entity_poly_seq", 1),
        ("test_procedural_sequence_mismatch_mse_met.cif", "atom_site_vs_entity_poly_seq", 1),
        ("test_cross_check_linked_number_obs_fallback_missing_refine_diffrn_id.cif", "linked_reflns_number_obs_1", 1),
        ("test_cross_check_linked_number_obs_fallback_loop_refine.cif", "linked_reflns_number_obs_1", 1),
        ("test_cross_check_linked_number_obs_keys_match.cif", "linked_reflns_number_obs_1", 1),
        ("test_cross_check_linked_number_obs_keys_disagree.cif", "linked_reflns_number_obs_1", 0),
        ("test_cross_check_linked_number_obs_two_refine_no_fallback.cif", "linked_reflns_number_obs_1", 0),
        ("test_cross_check_linked_number_obs_fallback_values_ok.cif", "linked_reflns_number_obs_1", 0),
    ],
)
def test_legacy_fixture_contract_through_public_engine(name, rule_id, expected):
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    report = engine.validate_path(FIXTURES / name)
    issues = [i for i in report.issues if i.rule_id == rule_id and i.check_type == "cross"]
    assert len(issues) == expected
