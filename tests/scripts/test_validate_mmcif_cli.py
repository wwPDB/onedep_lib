import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_mmcif.py"


def test_cli_compares_new_and_legacy_results_and_timings(tmp_path):
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
    model.write_text("data_model\n_cat.x 2\n_cat.y 1\n")
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
