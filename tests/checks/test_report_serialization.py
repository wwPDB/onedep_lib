import json

import pytest

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation


def test_report_serializes_rule_attribution_and_locations_without_changing_ok():
    report = CheckReport(
        "coordinates",
        [
            CheckIssue(
                CheckSeverity.ERROR,
                "CROSS_CHECK_VIOLATION",
                "bad",
                CifLocation("entry", "entity", "_entity.id", 0),
                check_type="cross",
                rule_id="uniq_entity_1",
                context={"keys": ["1"]},
            )
        ],
    )
    payload = json.loads(report.to_json())
    assert payload["issues"][0]["rule_id"] == "uniq_entity_1"
    assert payload["issues"][0]["location"]["row"] == 0
    assert payload["issues"][0]["severity"] == "error"
    assert report.ok is False


def test_report_rejects_non_json_values_instead_of_rendering_repr():
    report = CheckReport("s", [CheckIssue(CheckSeverity.ERROR, "X", "bad", actual=object())])
    with pytest.raises(TypeError, match="JSON"):
        report.to_json()
