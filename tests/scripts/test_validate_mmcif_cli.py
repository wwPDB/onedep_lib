import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_mmcif.py"


@pytest.mark.parametrize("profile", [False, True])
def test_cli_compares_new_and_legacy_results_and_timings(tmp_path, profile):
    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text("""data_test
_dictionary.title test
_dictionary.version 1
save_cat
_category.id cat
save_
save_cat_x
_item.name '_cat.x'
_item_type.code int
save_
save_cat_y
_item.name '_cat.y'
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
comparison cat x cat y 'x exceeds y' '>' hard .
""")
    model = tmp_path / "model.cif"
    model.write_text("data_model\n_cat.x 2\n_cat.y 1\ndata_second\n_cat.x 3\n_cat.y 1\n")
    legacy = tmp_path / "validate_mmcif.py"
    legacy.write_text("""from dataclasses import dataclass
from pathlib import Path

@dataclass
class Finding:
    message: str

def validate(dictionary, model):
    assert isinstance(dictionary, Path) and dictionary.name == 'dictionary.dic'
    assert isinstance(model, Path) and model.name == 'model.cif'
    return [Finding('legacy result')]
""")
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(dictionary),
            str(model),
            "--rules",
            str(rules),
            "--legacy-path",
            str(legacy),
            "-o",
            str(output),
            *(["--profile"] if profile else []),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    engines = json.loads(output.read_text())["engines"]
    assert set(engines) == {"onedep", "legacy"}
    assert engines["legacy"]["report"]["errors"] == [{"message": "legacy result"}]
    assert any(i["rule_id"] == "comparison" for i in engines["onedep"]["report"]["issues"])
    for name, record in engines.items():
        assert record["status"] == "completed"
        assert record["validation_seconds"] >= 0
        assert record["total_seconds"] >= record["validation_seconds"]
        assert name in result.stdout

    executions = engines["onedep"]["report"]["validation"]["rule_execution"]
    if profile:
        assert all(row["duration"] >= 0 for row in executions)
        timings = engines["onedep"]["cross_check_timings"]
        assert [row["rule_id"] for row in timings] == ["comparison"]
        assert timings[0]["total_seconds"] == sum(row["duration"] for row in executions)
        assert timings[0]["executions"] == 2
        assert "slowest first" in result.stdout
        assert "comparison" in result.stdout
        assert "cross_check_timings" not in engines["legacy"]
    else:
        assert all("duration" not in row for row in executions)
        assert "cross_check_timings" not in engines["onedep"]


def test_third_engine_runs_and_reports_after_another_engine_fails():
    from argparse import Namespace

    from scripts.validate_mmcif import PreparedEngine, run_engines

    args = Namespace(dictionary=Path("dictionary.dic"), mmcif=Path("model.cif"))

    def unavailable(_args):
        raise ImportError("engine unavailable")

    def third(config):
        assert config.dictionary == Path("dictionary.dic")
        return PreparedEngine(lambda: ["third finding"], lambda errors: {"errors": errors}, "third engine checks")

    results = run_engines(args, {"unavailable": unavailable, "third": third})
    assert results["unavailable"]["status"] == "failed"
    assert results["unavailable"]["failure_stage"] == "preparation"
    assert results["unavailable"]["validation_seconds"] is None
    assert results["third"]["status"] == "completed"
    assert results["third"]["report"] == {"errors": ["third finding"]}
    assert results["third"]["validation_seconds"] >= 0


def test_validation_failure_retains_timing_and_does_not_abort_other_engines():
    from argparse import Namespace

    from scripts.validate_mmcif import PreparedEngine, run_engines

    def fail():
        raise RuntimeError("validation failed")

    factories = {
        "broken": lambda _args: PreparedEngine(fail, lambda result: result, "broken"),
        "working": lambda _args: PreparedEngine(lambda: [], lambda result: {"errors": result}, "working"),
    }
    results = run_engines(Namespace(), factories)
    assert results["broken"]["failure_stage"] == "validation"
    assert results["broken"]["validation_seconds"] >= 0
    assert results["working"]["status"] == "completed"


def test_cross_check_timings_sum_blocks_and_rank_only_executed_rules():
    from scripts.validate_mmcif import cross_check_timings

    def execution(rule_id, duration, block_index):
        return {
            "rule_id": rule_id,
            "family": "pairwise",
            "block_index": block_index,
            "duration": duration,
            "rows_examined": 2,
            "findings_emitted": 1,
        }

    report = {
        "validation": {
            "rule_execution": [
                execution("slow", 0.2, 0),
                execution("fast", 0.3, 0),
                execution("slow", 0.2, 1),
                {"rule_id": "blocked", "status": "blocked"},
                {"rule_id": "excluded", "status": "excluded"},
            ]
        }
    }
    timings = cross_check_timings(report)
    assert [row["rule_id"] for row in timings] == ["slow", "fast"]
    assert timings[0] == {
        "rule_id": "slow",
        "family": "pairwise",
        "total_seconds": pytest.approx(0.4),
        "executions": 2,
        "rows_examined": 4,
        "findings_emitted": 2,
    }
    assert cross_check_timings({"validation": {"rule_execution": []}}) == []


@pytest.mark.parametrize("arguments", [("--native",), ("--engines", "legacy")])
def test_profile_rejects_engines_without_cross_check_instrumentation(arguments, capsys):
    from scripts.validate_mmcif import main

    with pytest.raises(SystemExit) as exc:
        main(["dictionary.dic", "model.cif", "--profile", *arguments])
    assert exc.value.code == 2
    assert "--profile requires" in capsys.readouterr().err


def test_profile_handles_input_failure_without_rule_timings(tmp_path, capsys):
    from unittest.mock import patch

    from scripts.validate_mmcif import PreparedEngine, main

    report = {"validation": {"rule_execution": []}}
    engine = PreparedEngine(lambda: report, lambda value: value, "input failed")
    output = tmp_path / "report.json"
    with patch.dict("scripts.validate_mmcif.ENGINE_FACTORIES", {"onedep": lambda args: engine}, clear=True):
        assert main(["dictionary.dic", "missing.cif", "--profile", "-o", str(output)]) == 0
    assert json.loads(output.read_text())["engines"]["onedep"]["cross_check_timings"] == []
    assert "No cross-checks were executed" in capsys.readouterr().out


def test_profile_explains_dictionary_blocking(tmp_path, capsys):
    from unittest.mock import patch

    from scripts.validate_mmcif import PreparedEngine, main

    report = {
        "validation": {
            "rule_execution": [
                {"rule_id": "comparison", "status": "blocked", "skip_reasons": {"blocked_dictionary": 1}}
            ],
        }
    }
    engine = PreparedEngine(lambda: report, lambda value: value, "dictionary and cross-checks")
    output = tmp_path / "report.json"
    with patch.dict("scripts.validate_mmcif.ENGINE_FACTORIES", {"onedep": lambda args: engine}, clear=True):
        assert main(["dictionary.dic", "model.cif", "--profile", "-o", str(output)]) == 0
    console = capsys.readouterr().out
    assert "Cross-checks blocked by dictionary errors: 1" in console
    assert "--cross-only" in console
