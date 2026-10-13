"""Concrete declarative evaluators using shared block-local projected access."""

from __future__ import annotations

from collections import defaultdict

from onedep_lib.validation.cif_access import tag
from onedep_lib.validation.models import ComparisonPlan, EnumerationPlan, PresencePlan
from onedep_lib.validation.runtime import OPS
from onedep_lib.validation.values import date, key, state, text


def comparison(plan: ComparisonPlan, run):
    source, target = run.table(plan.source), run.table(plan.target)
    if source is None or target is None:
        run.skip("category_absent", not_applicable=True)
        return
    family = plan.descriptor.family
    if family == "pairwise" and source is not target:
        summary = run.numeric_index(target, plan.target, plan.absolute)
    else:
        summary = None
    for row in run.rows(source):
        if family == "date_order":
            try:
                left, right = date(source.raw(plan.source, row)), date(target.raw(plan.target, row))
            except ValueError:
                run.unusable(source, plan.source, row, "invalid_date_operand")
                continue
            if left is not None and right is not None and not OPS[plan.operator](left, right):
                run.emit(
                    run.message(
                        plan.message,
                        left_value=text(source.raw(plan.source, row)),
                        right_value=text(target.raw(plan.target, row)),
                    ),
                    plan.severity,
                    source.location(plan.source, row),
                    related=(target.location(plan.target, row),),
                    context={"operator_role": "required_relation", "operator": plan.operator},
                )
            elif left is None or right is None:
                run.skip("operand_unfilled")
            continue
        fallback = False
        source_key = key(source.raw(plan.source_key, row)) if plan.source_key else None
        if plan.source_key and (plan.source.lower(), row) in run.breaks:
            run.skip("linked_break_group")
            continue
        if plan.source_key:
            matches = run.join(target, plan.target_key).get(source_key, []) if source_key is not None else []
            if not matches and plan.fallback and target.row_count == 1 and key(target.raw(plan.target_key, 0)) is None:
                if source_key is not None or source.row_count == 1:
                    matches, fallback = [0], True
            if not matches:
                run.skip("join_no_match")
                continue
        else:
            matches = [row]
        witness = None
        if plan.operator == "exists":
            if text(source.raw(plan.source, row)) is None:
                witness = next(
                    (
                        (r, text(target.raw(plan.target, r)))
                        for r in matches
                        if text(target.raw(plan.target, r)) is not None
                    ),
                    None,
                )
        else:
            left = run.numeric(source, plan.source, row, plan.absolute)
            if left is None:
                run.skip("operand_unfilled")
                continue
            if summary is not None:
                witness = summary.witness(left, plan.operator)
            elif plan.source_key and not fallback:
                witness = run.numeric_index(target, plan.target, plan.absolute, plan.target_key, source_key).witness(
                    left, plan.operator
                )
            else:
                for r in matches:
                    right = run.numeric(target, plan.target, r, plan.absolute)
                    if right is not None and OPS[plan.operator](left, right):
                        witness = (r, right)
                        break
        if witness is not None:
            r, value = witness
            run.emit(
                run.message(plan.message, item2CrossValue=text(target.raw(plan.target, r))),
                plan.severity,
                source.location(plan.source, row),
                related=(target.location(plan.target, r),),
                actual=text(source.raw(plan.source, row)),
                context={
                    "operator_role": "violation",
                    "operator": plan.operator,
                    "fallback_used": fallback,
                    "join_key": source_key,
                    "source_key_state": state(source.raw(plan.source_key, row)) if plan.source_key else None,
                    "target_key_state": state(target.raw(plan.target_key, r)) if plan.target_key else None,
                },
            )
            if plan.break_flag:
                run.breaks.add((plan.source.lower(), row))


def conditions_match(table, row, conditions, *, folded=False):
    for condition in conditions:
        value = text(table.raw(condition.item, row))
        if value is None:
            return False
        allowed = condition.values
        if folded:
            value, allowed = value.strip().casefold(), tuple(v.strip().casefold() for v in allowed)
        if value not in allowed:
            return False
    return True


def presence(plan: PresencePlan, run):
    table = run.view.table(plan.category)
    family = plan.descriptor.family
    if table is None or any(run.view.table(c) is not None for c in plan.skip_categories):
        run.skip("category_absent_or_superseded", not_applicable=True)
        return
    if plan.subtype and plan.subtype not in (run.context.entry_subtypes or ()):
        run.skip(
            "selector_context_unavailable" if run.context.entry_subtypes is None else "selector_mismatch",
            not_applicable=True,
        )
        return
    related = ()
    if plan.trigger:
        trigger_tag, value = plan.trigger
        trigger_table = run.table(trigger_tag)
        witness = (
            None
            if trigger_table is None
            else next(
                (r for r in range(trigger_table.row_count) if text(trigger_table.raw(trigger_tag, r)) == value), None
            )
        )
        if witness is None:
            run.skip("category_trigger_absent", not_applicable=True)
            return
        related = (trigger_table.location(trigger_tag, witness),)
    if family == "uniqueness":
        groups = defaultdict(list)
        for row in run.rows(table):
            parts = tuple(key(table.raw(item, row)) for item in plan.items)
            if all(p is not None for p in parts):
                groups[parts].append(row)
            else:
                run.skip("key_unfilled")
        for parts, duplicates in groups.items():
            if len(duplicates) > 1:
                for row in duplicates:
                    if not run.collector.poll():
                        return
                    run.emit(
                        run.message(
                            plan.message,
                            duplicate_display=",".join(parts),
                            key_columns=",".join(plan.items),
                            category=plan.category,
                        ),
                        plan.severity,
                        table.location(plan.items[0], row),
                        related=tuple(
                            table.location(plan.items[0], r)
                            for r in duplicates[: run.collector.options.max_related_locations]
                        ),
                        context={
                            "key": parts,
                            "group_count": len(duplicates),
                            "related_location_count": len(duplicates),
                            "related_locations_truncated": len(duplicates)
                            > run.collector.options.max_related_locations,
                        },
                    )
        return
    if family == "one_of":
        for row in run.rows(table):
            if any(text(table.raw(item, row)) is not None for item in plan.items):
                return
        run.emit(plan.message, plan.severity, table.location(plan.items[0], 0))
        return
    for row in run.rows(table):
        if plan.conditions and not conditions_match(table, row, plan.conditions):
            run.skip("condition_unmatched")
            continue
        if family == "make_mandatory":
            excluded = {tag(plan.category, i).lower() for i in plan.excluded}
            if not any(t not in excluded and text(table.raw(t, row)) is not None for t in table.tags):
                run.skip("row_has_no_signal")
                continue
        for item in plan.items:
            value = text(table.raw(item, row))
            if family == "conditional_regex" and value is None:
                run.skip("optional_target_unfilled")
                continue
            if value is None or plan.pattern is not None and plan.pattern.match(value) is None:
                run.emit(
                    plan.message,
                    plan.severity,
                    table.location(item, row),
                    related=related,
                    actual=value,
                    context={"value_state": state(table.raw(item, row))},
                )


def enumeration(plan: EnumerationPlan, run):
    source, target = run.table(plan.source), run.table(plan.target)
    family = plan.descriptor.family
    if source is None:
        run.skip("category_absent", not_applicable=True)
        return
    if family == "cross_reference":
        applicable = plan.mode == "all"
        if plan.mode == "coded":
            applicable = plan.code in (run.context.requested_codes or ())
        elif plan.mode is None:
            applicable = bool(set(plan.modes) & set(run.context.experiment_modes or ()))
        if not applicable:
            unavailable = (
                run.context.requested_codes is None if plan.mode == "coded" else run.context.experiment_modes is None
            )
            run.skip("selector_context_unavailable" if unavailable else "selector_mismatch", not_applicable=True)
            return
        membership = run.join(target, plan.target) if target else {}
    elif family == "dictionary_enum":
        if not any(text(source.raw(plan.source, r)) is not None for r in range(source.row_count)):
            run.skip("source_unfilled", not_applicable=True)
            return
        mapping = run.dictionary.cross_enums.get(plan.source.lower()) if run.dictionary else None
        if not mapping:
            run.skip("dictionary_cross_enum_unavailable", blocked=True)
            run.emit(
                "Dictionary cross-enumeration metadata unavailable",
                __import__("onedep_lib.checks.report", fromlist=["CheckSeverity"]).CheckSeverity.FATAL,
                source.location(plan.source),
                code="DICTIONARY_CROSS_ENUM_UNAVAILABLE",
                check_type="engine",
            )
            return
    for row in run.rows(source):
        value = text(source.raw(plan.source, row))
        if value is None:
            run.skip("source_unfilled")
            continue
        related = ()
        if family == "cross_reference":
            if value.strip() in membership:
                continue
            message, sev = plan.messages["default"]
            rendered = run.message(message, source_value=value, target_item=plan.target)
            target_value = None
        elif family == "conditional_enum":
            target_value = text(source.raw(plan.target, row))
            branch = next((b for b in plan.branches if b.driver == value), None)
            if branch is None or target_value is None or target_value in branch.values:
                continue
            rendered, sev = branch.message, branch.severity
        else:
            allowed = mapping.get(value)
            target_value = text(source.raw(plan.target, row))
            if not allowed:
                message, sev = plan.messages["unmapped"]
            elif target_value is None or target_value in allowed:
                continue
            else:
                message, sev = plan.messages["mismatch"]
            rendered = run.message(
                message,
                source_value=value,
                cross_item=plan.target,
                cross_value=target_value,
                allowed_values=", ".join(sorted(allowed or ())),
            )
        run.emit(
            rendered,
            sev,
            source.location(plan.source if family != "conditional_enum" else plan.target, row),
            related=related,
            actual=target_value,
            context={"source_value": value, "target_item": plan.target},
        )
