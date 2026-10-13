"""Bounded collection and block-local indexes for one validation run."""

from __future__ import annotations

import operator
from dataclasses import replace
from typing import Any

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation
from onedep_lib.validation.cif_access import BlockView, Table
from onedep_lib.validation.models import ValidationOptions
from onedep_lib.validation.values import key, number, render

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


class Collector:
    def __init__(self, report: CheckReport, options: ValidationOptions):
        self.report = report
        self.options = options
        self.stopped = False
        self.truncated = False
        self.cancelled = False
        self.observed = 0
        self.complete = True

    def poll(self) -> bool:
        if not self.stopped and self.options.cancelled and self.options.cancelled():
            self.cancelled = True
            self.terminal("VALIDATION_CANCELLED", "Validation cancelled")
        return not self.stopped

    def terminal(self, code: str, message: str):
        if not self.stopped:
            self.report.issues.append(CheckIssue(CheckSeverity.FATAL, code, message, check_type="engine"))
        self.stopped = True
        self.complete = False

    def add(self, issue: CheckIssue) -> bool:
        self.observed += 1
        if self.stopped:
            return False
        limit = self.options.max_issues
        if limit is not None and len(self.report.issues) >= limit:
            self.truncated = True
            self.terminal("VALIDATION_LIMIT_REACHED", "Validation issue retention limit reached")
            return False
        context = dict(issue.context)
        truncated_values = []

        def bounded(value, path):
            if isinstance(value, str) and len(value) > self.options.max_value_chars:
                truncated_values.append(path)
                return value[: self.options.max_value_chars]
            if isinstance(value, dict) or hasattr(value, "items"):
                metadata = {
                    "family",
                    "operator_role",
                    "operator",
                    "location_precision",
                    "reason",
                    "value_state",
                    "source_key_state",
                    "target_key_state",
                    "mapping_method",
                    "definition_tag",
                    "target_item",
                    "cross_item",
                    "backend",
                    "backend_version",
                    "diagnostic_adapter_version",
                    "diagnostic_digest",
                    "link_group",
                    "child_tags",
                    "parent_tags",
                    "message_values_truncated",
                    "truncated_values",
                }
                return {k: v if k in metadata else bounded(v, f"{path}.{k}") for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return tuple(bounded(v, f"{path}[{i}]") for i, v in enumerate(value))
            return value

        # Backend diagnostics have an independent limit.
        backend = context.pop("backend_diagnostic", None)
        context = bounded(context, "context")
        if backend is not None:
            context["backend_diagnostic"] = backend
        actual = bounded(issue.actual, "actual")
        expected = bounded(issue.expected, "expected")
        if truncated_values:
            context["truncated_values"] = tuple(truncated_values)
        related = issue.related_locations
        context["related_location_count"] = context.get("related_location_count", len(related))
        if len(related) > self.options.max_related_locations:
            context["related_locations_truncated"] = True
            related = related[: self.options.max_related_locations]
        self.report.issues.append(
            replace(issue, context=context, actual=actual, expected=expected, related_locations=related)
        )
        return True


class NumericIndex:
    """Linear summaries plus an order-preserving extrema tree for bounded witnesses."""

    def __init__(self, values: list[tuple[int, float]]):
        self.values = values
        self.first = {}
        if len(values) <= 1:
            return
        for row, value in values:
            self.first.setdefault(value, row)
        size = 1
        while size < len(values):
            size *= 2
        self.size = size
        self.minimum = [float("inf")] * (2 * size)
        self.maximum = [float("-inf")] * (2 * size)
        for i, (_, value) in enumerate(values):
            self.minimum[size + i] = self.maximum[size + i] = value
        for i in range(size - 1, 0, -1):
            self.minimum[i] = min(self.minimum[i * 2], self.minimum[i * 2 + 1])
            self.maximum[i] = max(self.maximum[i * 2], self.maximum[i * 2 + 1])

    def witness(self, left: float, op: str) -> tuple[int, float] | None:
        if len(self.values) <= 1:
            return self.values[0] if self.values and OPS[op](left, self.values[0][1]) else None
        if op == "==":
            row = self.first.get(left)
            return (row, left) if row is not None else None
        if not self.values:
            return None

        def possible(node):
            low, high = self.minimum[node], self.maximum[node]
            if low == float("inf"):
                return False
            if op == "!=":
                return low != left or high != left
            return OPS[op](left, high if op in ("<", "<=") else low)

        if not possible(1):
            return None
        node = 1
        while node < self.size:
            node = node * 2 if possible(node * 2) else node * 2 + 1
        return self.values[node - self.size]


class Run:
    def __init__(self, view: BlockView, collector: Collector, context, dictionary, formats):
        self.view = view
        self.collector = collector
        self.context = context
        self.dictionary = dictionary
        self.formats = formats
        self.records: list[dict[str, Any]] = []
        self.record: dict[str, Any] = {}
        self.breaks: set[tuple[str, int]] = set()
        self._message_truncated: list[str] = []

    def begin(self, descriptor):
        self.record = {
            "rule_id": descriptor.id,
            "family": descriptor.family,
            "block_index": self.view.index,
            "status": "evaluated",
            "rows_examined": 0,
            "findings_emitted": 0,
            "skip_reasons": {},
        }
        self.records.append(self.record)

    def skip(self, reason: str, *, blocked=False, not_applicable=False):
        reasons = self.record["skip_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1
        if blocked:
            self.record["status"] = "blocked"
            self.collector.complete = False
        elif not_applicable and self.record["status"] == "evaluated":
            self.record["status"] = "not_applicable"

    def rows(self, table: Table | None):
        if table is None:
            self.skip("category_absent", not_applicable=True)
            return
        for row in range(table.row_count):
            if row % 4096 == 0 and not self.collector.poll():
                return
            if self.collector.stopped:
                return
            self.record["rows_examined"] += 1
            yield row

    def table(self, item: str) -> Table | None:
        return self.view.table(item.split(".")[0])

    def message(self, template: str, **values) -> str:
        compact = {}
        self._message_truncated = []
        for name, value in values.items():
            display = str(value)
            if name not in ("target_item", "cross_item", "key_columns", "category") and (
                len(display) > self.collector.options.max_value_chars
            ):
                self._message_truncated.append(name)
                display = display[: self.collector.options.max_value_chars] + "…"
            compact[name] = display
        return render(template, **compact)

    def emit(
        self,
        message: str,
        severity: CheckSeverity,
        location: CifLocation,
        *,
        related=(),
        actual=None,
        expected=None,
        context=None,
        code="CROSS_CHECK_VIOLATION",
        check_type="cross",
    ):
        count = len(self.collector.report.issues)
        details = {"family": self.record["family"], "location_precision": "table"}
        details.update(context or {})
        if self._message_truncated:
            details["message_values_truncated"] = tuple(self._message_truncated)
            self._message_truncated = []
        self.collector.add(
            CheckIssue(
                severity,
                code,
                message,
                location,
                expected,
                actual,
                check_type,
                self.record["rule_id"],
                tuple(related),
                details,
            )
        )
        if len(self.collector.report.issues) > count:
            self.record["findings_emitted"] += 1

    def unusable(self, table: Table, item: str, row: int, reason: str):
        self.skip(reason, blocked=True)
        self.emit(
            "Cross-check input cannot be evaluated",
            CheckSeverity.ERROR,
            table.location(item, row),
            actual=table.raw(item, row),
            context={"reason": reason},
            code="CROSS_INPUT_UNUSABLE",
            check_type="engine",
        )

    def numeric(self, table: Table, item: str, row: int, absolute=False):
        try:
            value = number(table.raw(item, row))
            return abs(value) if value is not None and absolute else value
        except ValueError:
            self.unusable(table, item, row, "invalid_numeric_operand")
            return None

    def join(self, table: Table, item: str):
        signature = ("join", table.table_index, item.lower(), "trim_exact", "filled")
        if signature not in self.view.indexes:
            index = {}
            for row in range(table.row_count):
                if row % 4096 == 0 and not self.collector.poll():
                    break
                value = key(table.raw(item, row))
                if value is not None:
                    index.setdefault(value, []).append(row)
            self.view.indexes[signature] = index
            self.view.index_builds += 1
        return self.view.indexes[signature]

    def numeric_index(self, table: Table, item: str, absolute=False, key_item=None, join_key=None):
        signature = ("numeric", table.table_index, item.lower(), absolute, key_item.lower() if key_item else None)
        if signature not in self.view.indexes:
            group_rows = self.join(table, key_item) if key_item else {None: range(table.row_count)}
            groups = {}
            invalid = False
            for group_key, indices in group_rows.items():
                if self.collector.stopped:
                    break
                values = []
                for index, row in enumerate(indices):
                    if index % 4096 == 0 and not self.collector.poll():
                        break
                    before = self.record["skip_reasons"].get("invalid_numeric_operand", 0)
                    value = self.numeric(table, item, row, absolute)
                    invalid |= self.record["skip_reasons"].get("invalid_numeric_operand", 0) > before
                    if value is not None:
                        values.append((row, value))
                groups[group_key] = NumericIndex(values)
            self.view.indexes[signature] = (groups, invalid)
            self.view.index_builds += 1
        groups, invalid = self.view.indexes[signature]
        if invalid:
            self.skip("invalid_numeric_column", blocked=True)
        if join_key in groups:
            return groups[join_key]
        return NumericIndex([])
