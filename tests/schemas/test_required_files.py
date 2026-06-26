"""
Tests for the feedback strings in the required_files JSON schema.

The runner extracts human-readable messages by looking up error.validator in
error.schema["feedback"].  For a feedback string to be surfaced, it must be a
sibling of the validator keyword that actually runs.  These tests verify:

  - valid inputs produce no errors
  - each feedback key surfaces the expected message

Known schema issues documented by these tests:
  Invalid file type triggers two enum errors (global items + method-specific items).
  (however, retained for sake of explainability)
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from onedep_lib.config import DepositConfig


@pytest.fixture(scope="module")
def schema() -> dict:
    schema_path : Path  = DepositConfig().local_schema_cache_dir / "required_files.json"
    with schema_path.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def validator(schema: dict) -> jsonschema.Draft202012Validator:
    schema_dir = DepositConfig().local_schema_cache_dir
    xray_schema = json.loads((schema_dir / "xray.json").read_text())
    registry = Registry().with_resources([
        ("xray.json", Resource(contents=xray_schema, specification=DRAFT202012))
    ])
    return jsonschema.Draft202012Validator(schema, registry=registry)


def _messages(validator: jsonschema.Draft202012Validator, data: dict) -> list[str]:
    """Return all feedback messages surfaced by the runner's extraction logic."""
    messages = []
    for error in validator.iter_errors(data):
        fb = error.schema.get("feedback", {})
        msg = fb.get(error.validator)
        if msg:
            messages.append(msg)
    return messages


# ---------------------------------------------------------------------------
# Valid inputs
# ---------------------------------------------------------------------------

class TestValidInputs:
    def test_xray_valid(self, validator):
        data = {"method": "xray", "files": ["co-cif", "xs-cif"]}
        assert _messages(validator, data) == []

    def test_xray_mtz_valid(self, validator):
        data = {"method": "xray", "files": ["co-cif", "xs-mtz"]}
        assert _messages(validator, data) == []

    def test_neutron_valid(self, validator):
        data = {"method": "neutron", "files": ["co-cif", "xs-cif"]}
        assert _messages(validator, data) == []

    def test_fiber_valid(self, validator):
        data = {"method": "fiber", "files": ["co-cif", "xs-cif"]}
        assert _messages(validator, data) == []

    def test_em_spa_valid(self, validator):
        data = {
            "method": "em",
            "subtype": "single",
            "files": ["co-cif", "vo-map", "img-emdb", "half-map", "half-map"],
        }
        assert _messages(validator, data) == []

    def test_em_helical_valid(self, validator):
        data = {
            "method": "em",
            "subtype": "helical",
            "files": ["co-cif", "vo-map", "img-emdb", "half-map", "half-map"],
        }
        assert _messages(validator, data) == []

    def test_em_tomography_no_halfmaps_valid(self, validator):
        data = {
            "method": "em",
            "subtype": "tomography",
            "files": ["vo-map", "img-emdb"],
        }
        assert _messages(validator, data) == []

    def test_ec_valid(self, validator):
        data = {"method": "ec", "files": ["co-cif", "xs-cif"]}
        assert _messages(validator, data) == []

    def test_nmr_with_shifts_and_restraints_valid(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-shi", "nm-res-amb"]}
        assert _messages(validator, data) == []

    def test_nmr_with_unified_nef_valid(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-uni-nef"]}
        assert _messages(validator, data) == []

    def test_ssnmr_with_unified_star_valid(self, validator):
        data = {"method": "ssnmr", "files": ["co-cif", "nm-uni-str"]}
        assert _messages(validator, data) == []


# ---------------------------------------------------------------------------
# Method enum feedback
# ---------------------------------------------------------------------------

class TestMethodFeedback:
    def test_unknown_method_surfaces_feedback(self, validator):
        data = {"method": "cryo-et", "files": ["co-cif"]}
        msgs = _messages(validator, data)
        assert "unknown method" in msgs

    def test_unknown_subtype_surfaces_feedback(self, validator):
        data = {"method": "em", "subtype": "badtype", "files": ["vo-map", "img-emdb"]}
        msgs = _messages(validator, data)
        assert "unknown subtype" in msgs


# ---------------------------------------------------------------------------
# File type enum feedback
# ---------------------------------------------------------------------------

class TestFileTypeFeedback:
    def test_globally_unknown_file_type(self, validator):
        data = {"method": "xray", "files": ["co-cif", "xs-cif", "BADFILE"]}
        msgs = _messages(validator, data)
        assert "unknown file type" in msgs

    def test_globally_unknown_file_type_returns_two_enum_errors(self, validator):
        """
        Known issue: an invalid type triggers both the global items.enum and the
        method-specific items.enum, producing duplicate messages.
        """
        data = {"method": "xray", "files": ["co-cif", "xs-cif", "BADFILE"]}
        msgs = _messages(validator, data)
        enum_msgs = [m for m in msgs if "unknown file type" in m]
        assert len(enum_msgs) == 2, (
            "Expected two enum messages (global + xray-specific) for an unknown file type"
        )

    def test_xray_specific_file_type_message(self, validator):
        data = {"method": "xray", "files": ["co-cif", "xs-cif", "vo-map"]}
        msgs = _messages(validator, data)
        assert "unknown file type for xray diffraction" in msgs

    def test_em_specific_file_type_message(self, validator):
        data = {"method": "em", "subtype": "tomography", "files": ["vo-map", "img-emdb", "nm-shi"]}
        msgs = _messages(validator, data)
        assert "unknown file type for em" in msgs

    def test_nmr_specific_file_type_message(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-shi", "nm-res-amb", "vo-map"]}
        msgs = _messages(validator, data)
        assert "unknown file type for nmr" in msgs

    def test_ec_specific_file_type_message(self, validator):
        data = {"method": "ec", "files": ["co-cif", "xs-cif", "vo-map"]}
        msgs = _messages(validator, data)
        assert "unknown file type for ec" in msgs


# ---------------------------------------------------------------------------
# contains feedback (triggers when 0 items match)
# ---------------------------------------------------------------------------

class TestContainsFeedback:
    def test_xray_missing_coordinates(self, validator):
        data = {"method": "xray", "files": ["xs-cif"]}
        msgs = _messages(validator, data)
        assert "one coordinates file is required for xray" in msgs

    def test_xray_missing_reflections(self, validator):
        data = {"method": "xray", "files": ["co-cif"]}
        msgs = _messages(validator, data)
        assert "one reflections file is required for xray" in msgs

    def test_neutron_missing_coordinates(self, validator):
        data = {"method": "neutron", "files": ["xs-cif"]}
        msgs = _messages(validator, data)
        assert "one coordinates file is required for neutron" in msgs

    def test_neutron_missing_reflections(self, validator):
        data = {"method": "neutron", "files": ["co-cif"]}
        msgs = _messages(validator, data)
        assert "one reflections file is required for neutron" in msgs

    def test_fiber_missing_coordinates(self, validator):
        data = {"method": "fiber", "files": ["xs-cif"]}
        msgs = _messages(validator, data)
        assert "one coordinates file is required for fiber" in msgs

    def test_fiber_missing_reflections(self, validator):
        data = {"method": "fiber", "files": ["co-cif"]}
        msgs = _messages(validator, data)
        assert "one reflections file is required for fiber" in msgs

    def test_em_missing_map(self, validator):
        data = {"method": "em", "subtype": "tomography", "files": ["img-emdb"]}
        msgs = _messages(validator, data)
        assert "a map file is required for em" in msgs

    def test_em_missing_image(self, validator):
        data = {"method": "em", "subtype": "tomography", "files": ["vo-map"]}
        msgs = _messages(validator, data)
        assert "an image file is required for em" in msgs

    def test_em_spa_missing_halfmaps(self, validator):
        data = {"method": "em", "subtype": "single", "files": ["vo-map", "img-emdb"]}
        msgs = _messages(validator, data)
        assert "half-map is required for single, helical, and subtomogram" in msgs

    def test_ec_missing_reflections(self, validator):
        data = {"method": "ec", "files": ["co-cif"]}
        msgs = _messages(validator, data)
        assert "one reflections file is required for ec" in msgs

    def test_nmr_missing_chemical_shifts(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-res-amb"]}
        msgs = _messages(validator, data)
        assert "one chemical shifts file is required for nmr" in msgs

    def test_nmr_missing_restraints(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-shi"]}
        msgs = _messages(validator, data)
        assert "one restraints file is required for nmr" in msgs


# ---------------------------------------------------------------------------
# minContains feedback (only triggers when minContains >= 2)
# ---------------------------------------------------------------------------

class TestMinContainsFeedback:
    def test_em_spa_one_halfmap_triggers_mincontains(self, validator):
        """
        minContains:2 triggers when exactly 1 half-map is present (not 0, where
        contains triggers instead).
        """
        data = {"method": "em", "subtype": "single", "files": ["vo-map", "img-emdb", "half-map"]}
        msgs = _messages(validator, data)
        assert "exactly two half-map files are required for single, helical, and subtomogram" in msgs


# ---------------------------------------------------------------------------
# maxContains feedback
# ---------------------------------------------------------------------------

class TestMaxContainsFeedback:
    def test_xray_two_coordinate_files(self, validator):
        data = {"method": "xray", "files": ["co-cif", "co-cif", "xs-cif"]}
        msgs = _messages(validator, data)
        assert "only one coordinates file is allowed for xray" in msgs

    def test_xray_two_reflections_files(self, validator):
        data = {"method": "xray", "files": ["co-cif", "xs-cif", "xs-mtz"]}
        msgs = _messages(validator, data)
        assert "only one reflections file is allowed for xray" in msgs

    def test_em_two_reflections_files(self, validator):
        data = {
            "method": "em",
            "subtype": "tomography",
            "files": ["vo-map", "img-emdb", "xs-cif", "xs-mtz"],
        }
        msgs = _messages(validator, data)
        assert "only one reflections file is allowed for em" in msgs

    def test_em_spa_three_halfmaps(self, validator):
        data = {
            "method": "em",
            "subtype": "single",
            "files": ["vo-map", "img-emdb", "half-map", "half-map", "half-map"],
        }
        msgs = _messages(validator, data)
        assert "exactly two half-map files are required for single, helical, and subtomogram" in msgs

    def test_nmr_two_unified_files(self, validator):
        data = {"method": "nmr", "files": ["co-cif", "nm-uni-nef", "nm-uni-nef"]}
        msgs = _messages(validator, data)
        assert "only one unified file is allowed for nmr" in msgs


# ---------------------------------------------------------------------------
# required feedback
# ---------------------------------------------------------------------------

class TestRequiredFeedback:
    def test_em_missing_subtype(self, validator):
        data = {"method": "em", "files": ["vo-map", "img-emdb"]}
        msgs = _messages(validator, data)
        assert "subtype is required for em" in msgs



