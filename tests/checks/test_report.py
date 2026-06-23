from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation


def test_check_report_ok_when_no_issues():
    report = CheckReport(source="file-123")
    assert report.ok is True
    assert report.issues == []


def test_check_report_not_ok_on_error():
    issue = CheckIssue(
        severity=CheckSeverity.ERROR,
        code="TEST.ERROR",
        message="Something went wrong",
    )
    report = CheckReport(source="file-123", issues=[issue])
    assert report.ok is False


def test_check_report_not_ok_on_fatal():
    issue = CheckIssue(severity=CheckSeverity.FATAL, code="X", message="Fatal")
    report = CheckReport(source="s", issues=[issue])
    assert report.ok is False


def test_check_report_ok_with_warnings_only():
    issue = CheckIssue(severity=CheckSeverity.WARNING, code="X", message="Warn")
    report = CheckReport(source="s", issues=[issue])
    assert report.ok is True


def test_check_report_errors_filters_correctly():
    issues = [
        CheckIssue(CheckSeverity.INFO, "I", "info msg"),
        CheckIssue(CheckSeverity.WARNING, "W", "warn msg"),
        CheckIssue(CheckSeverity.ERROR, "E", "error msg"),
        CheckIssue(CheckSeverity.FATAL, "F", "fatal msg"),
    ]
    report = CheckReport(source="s", issues=issues)
    errors = report.errors()
    assert len(errors) == 2
    assert all(i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL) for i in errors)


def test_check_report_warnings_filters_correctly():
    issues = [
        CheckIssue(CheckSeverity.WARNING, "W1", "w1"),
        CheckIssue(CheckSeverity.ERROR, "E1", "e1"),
    ]
    report = CheckReport(source="s", issues=issues)
    warnings = report.warnings()
    assert len(warnings) == 1
    assert warnings[0].code == "W1"


def test_cif_location_defaults_to_none():
    loc = CifLocation()
    assert loc.data_block is None
    assert loc.category is None
    assert loc.item is None
    assert loc.row is None
    assert loc.line is None
    assert loc.column is None


def test_check_issue_with_location():
    loc = CifLocation(category="atom_site", item="Cartn_x", row=0)
    issue = CheckIssue(
        severity=CheckSeverity.ERROR,
        code="MMCIF.TYPE_MISMATCH",
        message="Expected float",
        location=loc,
        expected=1.0,
        actual="bad",
    )
    assert issue.location.category == "atom_site"
    assert issue.expected == 1.0
    assert issue.actual == "bad"


def test_check_issue_coerces_raw_string_severity():
    issue = CheckIssue(severity="error", code="X", message="msg")
    assert issue.severity is CheckSeverity.ERROR
    report = CheckReport(source="s", issues=[issue])
    assert report.ok is False
