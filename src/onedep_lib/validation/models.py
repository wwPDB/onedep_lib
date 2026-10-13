"""Prepared resources and execution records for local mmCIF validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable

from onedep_lib.checks.report import CheckSeverity, freeze


class ValidationConfigurationError(ValueError):
    """A dictionary, rule bundle or validation option is invalid."""


@dataclass(frozen=True)
class ValidationOptions:
    phases: tuple[str, ...] = ("dictionary", "cross")
    dictionary_failure_policy: str = "stop_block"
    blocks: tuple[str, ...] | None = None
    max_issues: int | None = 10000
    max_value_chars: int = 1024
    max_diagnostic_chars: int = 4096
    max_related_locations: int = 32
    max_alignment_cells: int = 1000000
    unknown_tag_severity: CheckSeverity = CheckSeverity.WARNING
    dictionary_context_checks: bool = False
    dictionary_parent_checks: bool = True
    dictionary_deposition_overrides: bool = False
    profile: bool = False
    cancelled: Callable[[], bool] | None = field(default=None, compare=False, repr=False)
    rule_dictionary_policy: str = "error"

    def __post_init__(self):
        if self.rule_dictionary_policy not in ("error", "block_rule"):
            raise ValidationConfigurationError("rule_dictionary_policy must be error or block_rule")
        if isinstance(self.phases, str):
            raise ValidationConfigurationError("phases must be an ordered collection")
        if self.cancelled is not None and not callable(self.cancelled):
            raise ValidationConfigurationError("cancelled must be a zero-argument callable")
        for name in (
            "dictionary_context_checks",
            "dictionary_parent_checks",
            "dictionary_deposition_overrides",
            "profile",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValidationConfigurationError(f"{name} must be boolean")
        object.__setattr__(self, "phases", tuple(self.phases))
        if self.phases not in (("dictionary", "cross"), ("dictionary",), ("cross",)):
            raise ValidationConfigurationError("phases must be dictionary, cross, or dictionary then cross")
        if self.dictionary_failure_policy != "stop_block":
            raise ValidationConfigurationError("Only stop_block is certified; continue_safe is not yet supported")
        for name in (
            "max_issues",
            "max_value_chars",
            "max_diagnostic_chars",
            "max_related_locations",
            "max_alignment_cells",
        ):
            value = getattr(self, name)
            if value is None and name == "max_issues":
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationConfigurationError(f"{name} must be a nonnegative integer")
        if self.blocks is not None:
            object.__setattr__(self, "blocks", tuple(self.blocks))
            if len(set(self.blocks)) != len(self.blocks):
                raise ValidationConfigurationError("Duplicate selected block names")
        try:
            severity = CheckSeverity(self.unknown_tag_severity)
        except ValueError as exc:
            raise ValidationConfigurationError("Invalid unknown_tag_severity") from exc
        if severity not in (CheckSeverity.WARNING, CheckSeverity.ERROR):
            raise ValidationConfigurationError("unknown_tag_severity must be warning or error")
        object.__setattr__(self, "unknown_tag_severity", severity)


MODES = frozenset(("xray", "nd", "fd", "em", "ec", "nmr", "ssnmr"))
SUBTYPES = frozenset(("EC", "EM-helical", "EM-single_part", "EM-subtomogram"))


@dataclass(frozen=True)
class ValidationContext:
    experiment_modes: tuple[str, ...] | None = None
    entry_subtypes: tuple[str, ...] | None = None
    requested_codes: tuple[str, ...] | None = None
    block_overrides: Mapping[str, ValidationContext] = field(default_factory=dict)

    def __post_init__(self):
        for name, allowed in (
            ("experiment_modes", MODES),
            ("entry_subtypes", SUBTYPES),
            ("requested_codes", frozenset(("PDB", "EMDB", "BMRB"))),
        ):
            value = getattr(self, name)
            if value is not None:
                value = tuple(value)
                if any(v not in allowed for v in value):
                    raise ValidationConfigurationError(f"Unknown {name} token")
                object.__setattr__(self, name, value)
        overrides = dict(self.block_overrides)
        if any(not isinstance(v, ValidationContext) or v.block_overrides for v in overrides.values()):
            raise ValidationConfigurationError("Block overrides must be flat ValidationContext values")
        object.__setattr__(self, "block_overrides", MappingProxyType(overrides))


@dataclass(frozen=True)
class RuleDescriptor:
    id: str
    family: str
    source_category: str
    source_row: int
    projected_tags: tuple[str, ...]
    selector_dependencies: tuple[str, ...] = ()
    check_category: str | None = None


@dataclass(frozen=True)
class PreparedRules:
    bundle_id: str
    schema_version: str
    version: str
    digest: str
    rules: tuple[RuleDescriptor, ...]
    legacy_defaults_used: bool = False
    defaults_digest: str | None = None

    @property
    def root_rule_count(self) -> int:
        return len(self.rules)


@dataclass(frozen=True)
class Condition:
    item: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Branch:
    ordinal: int
    message: str
    severity: CheckSeverity
    driver: str | None = None
    protocol: str | None = None
    requires: str | None = None
    predicate: str | None = None
    substring: str | None = None
    values: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class ComparisonPlan:
    descriptor: RuleDescriptor
    source: str
    target: str
    operator: str
    message: str
    severity: CheckSeverity
    source_key: str | None = None
    target_key: str | None = None
    fallback: str | None = None
    absolute: bool = False
    break_flag: bool = False


@dataclass(frozen=True)
class PresencePlan:
    descriptor: RuleDescriptor
    category: str
    items: tuple[str, ...]
    message: str
    severity: CheckSeverity = CheckSeverity.ERROR
    conditions: tuple[Condition, ...] = ()
    excluded: tuple[str, ...] = ()
    skip_categories: tuple[str, ...] = ()
    subtype: str | None = None
    trigger: tuple[str, str] | None = None
    pattern: Any = None


@dataclass(frozen=True)
class EnumerationPlan:
    descriptor: RuleDescriptor
    source: str
    target: str
    branches: tuple[Branch, ...] = ()
    mode: str | None = None
    code: str | None = None
    modes: tuple[str, ...] = ()
    messages: Mapping[str, tuple[str, CheckSeverity]] = field(default_factory=dict)


@dataclass(frozen=True)
class ProceduralPlan:
    descriptor: RuleDescriptor
    kind: str
    category: str | None
    item: str | None
    driver: str | None
    branches: tuple[Branch, ...]
    limits: Mapping[str, tuple[float, str, CheckSeverity]]
    empty_branches: tuple[Branch, ...]
    source: str | None = None
    target: str | None = None
    source_key: str | None = None
    target_key: str | None = None
    severity: CheckSeverity = CheckSeverity.ERROR
    mismatch_message: str = ""
    missing_message: str = ""


@dataclass(frozen=True)
class Format:
    pattern: Any
    normalization: str
    match_mode: str


@dataclass(frozen=True)
class ValidationMetadata:
    complete: bool
    valid: bool
    requested_phases: tuple[str, ...]
    selected_blocks: tuple[str, ...] = ()
    excluded_blocks: tuple[str, ...] = ()
    engine_version: str = "1.1.0"
    backend_version: str = ""
    schema_version: str = "1.0"
    dictionaries: tuple[Mapping[str, Any], ...] = ()
    rules: PreparedRules | None = None
    input_digest: str | None = None
    resource_fingerprint: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)
    phases: tuple[Mapping[str, Any], ...] = ()
    rule_execution: tuple[Mapping[str, Any], ...] = ()
    resolved_context: Mapping[str, Any] = field(default_factory=dict)
    counters: Mapping[str, Any] = field(default_factory=dict)
    truncated: bool = False
    issues_observed: int = 0
    errors: int = 0
    warnings: int = 0
    dictionary_coverage: Mapping[str, Any] = field(default_factory=dict)
    requested_categories: tuple[str, ...] | None = None

    def __post_init__(self):
        for name in (
            "dictionaries",
            "options",
            "phases",
            "rule_execution",
            "resolved_context",
            "counters",
            "dictionary_coverage",
            "requested_categories",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))
