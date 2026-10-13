from onedep_lib.checks.report import CheckReport
from onedep_lib.config import DepositConfig
from onedep_lib.dsp import deposit_init
from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.validation import MmCifValidator, ValidationOptions
from tests.unit.apis.deposit.test_stub_api_client import StubApiClient


class RequiredOnlyRunner:
    def check_required_files(self, files, experiment_type, em_subtype=None):
        return CheckReport("required")


def test_submission_records_advisory_issues_and_revalidates_current_contents(tmp_path):
    model = tmp_path / "model.cif"
    model.write_text("data_x\nloop_\n_entity.id\n1\n1\n")
    engine = MmCifValidator.load([], options=ValidationOptions(phases=("cross",)))
    dep = deposit_init(
        "x@example.org",
        [],
        Country.USA,
        DepositConfig(session_dir=tmp_path / "sessions"),
        ExperimentType.XRAY,
        _api_client=StubApiClient(),
        _check_runner=RequiredOnlyRunner(),
        mmcif_validator=engine,
    )
    file_id = dep.add_file(str(model), FileType.MMCIF_COORD)
    assert not dep.check_mmcif_file(file_id).ok
    model.write_text("data_x\n_entity.id 1\n")
    assert dep.deposit() == "D_999"
    automatic_report = dep.metadata_report[file_id]
    assert automatic_report.ok
    assert automatic_report.validation is not None
    assert automatic_report.validation.complete
    assert dep.check_required_files().source == "required"


def test_config_loads_dictionary_paths_and_per_type_rule_mapping(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""[default]
mmcif_dictionary_paths = ["dict.dic"]
[default.mmcif_rule_paths]
co-cif = "coordinates.cif"
xs-cif = "reflections.cif"
""")
    config = DepositConfig.load(config_path=config_file)
    assert config.mmcif_dictionary_paths == (tmp_path / "dict.dic",)
    assert config.mmcif_rule_paths["co-cif"] == tmp_path / "coordinates.cif"


def test_configured_dictionary_failure_does_not_block_submission(tmp_path, caplog):
    config = DepositConfig(session_dir=tmp_path / "sessions", mmcif_dictionary_paths=(tmp_path / "missing.dic",))
    model = tmp_path / "model.cif"
    model.write_text("data_x\n_entity.id 1\n")
    dep = deposit_init(
        "x@example.org",
        [],
        Country.USA,
        config,
        ExperimentType.XRAY,
        _api_client=StubApiClient(),
        _check_runner=RequiredOnlyRunner(),
    )
    file_id = dep.add_file(str(model), FileType.MMCIF_COORD)
    assert dep.deposit() == "D_999"
    report = dep.metadata_report[file_id]
    assert report.issues[0].code == "VALIDATION_UNAVAILABLE"
    assert report.validation is not None
    assert not report.validation.complete
    assert "metadata_report" in caplog.text


def test_unmapped_reflections_receive_dictionary_checks_with_explicit_partial_coverage(tmp_path):
    from tests.validation.test_dictionary import DICTIONARY

    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)
    config = DepositConfig(session_dir=tmp_path / "sessions", mmcif_dictionary_paths=(dictionary,))
    model = tmp_path / "reflections.cif"
    model.write_text("data_x\n_cat.id 1\n_cat.value 1.0\n")
    dep = deposit_init(
        "x@example.org",
        [],
        Country.USA,
        config,
        ExperimentType.XRAY,
        _api_client=StubApiClient(),
        _check_runner=RequiredOnlyRunner(),
    )
    file_id = dep.add_file(str(model), FileType.CRYSTAL_STRUC_FACTORS)
    report = dep.check_mmcif_file(file_id)
    assert report.issues[-1].code == "CROSS_RULESET_UNCONFIGURED"
    assert report.validation is not None
    assert report.validation.phases[0]["status"] == "completed"
    assert report.validation is not None
    assert not report.validation.complete
    assert dep.deposit() == "D_999"


def test_custom_rule_files_are_selected_by_declared_file_type(tmp_path):
    from tests.validation.test_dictionary import DICTIONARY

    dictionary = tmp_path / "dictionary.dic"
    dictionary.write_text(DICTIONARY)

    def rule_path(name, op):
        path = tmp_path / (name + ".cif")
        path.write_text(f"""data_rules
loop_
_validator_rule_pairwise.id
_validator_rule_pairwise.source_category
_validator_rule_pairwise.source_item
_validator_rule_pairwise.target_category
_validator_rule_pairwise.target_item
_validator_rule_pairwise.operator
_validator_rule_pairwise.severity
_validator_rule_pairwise.message
{name} cat value cat id {op} error '{name} violated'
""")
        return path

    config = DepositConfig(
        session_dir=tmp_path / "sessions",
        mmcif_dictionary_paths=(dictionary,),
        mmcif_rule_paths={"co-cif": rule_path("coordinate", ">"), "xs-cif": rule_path("reflections", "<")},
    )
    path = tmp_path / "model.cif"
    path.write_text("data_x\n_cat.id 1\n_cat.value 2\n")
    dep = deposit_init(
        "x@example.org",
        [],
        Country.USA,
        config,
        ExperimentType.XRAY,
        _api_client=StubApiClient(),
        _check_runner=RequiredOnlyRunner(),
    )
    coordinate = dep.add_file(str(path), FileType.MMCIF_COORD)
    reflections = dep.add_file(str(path), FileType.CRYSTAL_STRUC_FACTORS)
    assert dep.deposit() == "D_999"
    coordinate_report = dep.metadata_report[coordinate]
    reflection_report = dep.metadata_report[reflections]
    assert [i.rule_id for i in coordinate_report.issues] == ["coordinate"]
    assert reflection_report.issues == []
    assert reflection_report.validation is not None
    assert coordinate_report.validation is not None
    assert reflection_report.validation.complete
    assert coordinate_report.validation.rules is not None
    assert reflection_report.validation.rules is not None
    assert coordinate_report.validation.rules.digest != reflection_report.validation.rules.digest
