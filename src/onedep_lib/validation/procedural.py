"""Cohesive procedural algorithms; thresholds, branches and wording come from CIF."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from onedep_lib.checks.report import CheckSeverity
from onedep_lib.validation.evaluators import conditions_match
from onedep_lib.validation.values import key, text


def display_number(value):
    return f"{round(value, 4):.4f}".rstrip("0").rstrip(".")


def integer_identity(value):
    if re.fullmatch(r"[+-]?[0-9]+", value) is None:
        return None
    digits = value.lstrip("+-").lstrip("0") or "0"
    return ("-" if value.startswith("-") and digits != "0" else "") + digits


def identity(table, row):
    model = key(table.raw("pdbx_PDB_model_num", row)) or "1"
    chain = key(table.raw("label_asym_id", row))
    entity = key(table.raw("label_entity_id", row))
    label = key(table.raw("label_seq_id", row))
    auth_chain = key(table.raw("auth_asym_id", row))
    auth_residue = key(table.raw("auth_seq_id", row))
    insertion = key(table.raw("pdbx_PDB_ins_code", row)) or ""
    atom = key(table.raw("label_atom_id", row)) or key(table.raw("auth_atom_id", row))
    component = text(table.raw("auth_comp_id", row)) or text(table.raw("label_comp_id", row)) or ""
    chain_display = auth_chain or chain or ""
    residue_display = (auth_residue or label or "") + insertion
    canonical_model = integer_identity(model)
    canonical_label = integer_identity(label) if label is not None else None
    if canonical_model is None or label is not None and canonical_label is None:
        residue_key = None
    elif chain and label:
        residue_key = (canonical_model, chain, canonical_label, insertion)
    elif chain and auth_residue:
        residue_key = (canonical_model, chain, ("author", auth_residue), insertion)
    elif entity and auth_chain and auth_residue:
        residue_key = (canonical_model, (entity, auth_chain), ("author", auth_residue), insertion)
    else:
        residue_key = None
    return (
        residue_key + (atom,) if residue_key and atom else None,
        {"chain": chain_display, "residue": residue_display, "comp": component, "atom": atom or "", "model": model},
    )


def add_partial(partials, value):
    # Error-free partial sums, retaining O(number of floating exponents) per group.
    index = 0
    for previous in partials:
        if abs(value) < abs(previous):
            value, previous = previous, value
        high = value + previous
        low = previous - (high - value)
        if low:
            partials[index] = low
            index += 1
        value = high
    partials[index:] = [value]


def occupancy(plan, run, table):
    totals = {}
    for row in run.rows(table):
        value = run.numeric(table, plan.item, row)
        if value is None:
            continue
        group, _ = identity(table, row)
        if group is None:
            run.skip("atom_identity_unavailable", blocked=True)
            run.emit(
                "Atom identity unavailable for occupancy grouping",
                CheckSeverity.FATAL,
                table.location(plan.item, row),
                code="CROSS_INPUT_UNUSABLE",
                check_type="engine",
            )
            continue
        partials, count, witnesses = totals.get(group, ([], 0, []))
        add_partial(partials, value)
        if len(witnesses) < run.collector.options.max_related_locations:
            witnesses.append(row)
        totals[group] = (partials, count + 1, witnesses)
    sums = {
        group: (round(math.fsum(parts), 4), count, witnesses) for group, (parts, count, witnesses) in totals.items()
    }
    high_limit, high_message, high_severity = plan.limits["over_one"]
    low_limit, low_message, low_severity = plan.limits["below"]
    # Second projected pass retrieves only retained finding context.
    for row in run.rows(table):
        value = run.numeric(table, plan.item, row)
        if value is None:
            continue
        group, display = identity(table, row)
        if group in sums:
            total, count, witnesses = sums[group]
            if total > high_limit:
                run.emit(
                    run.message(high_message, **display, total=display_number(total)),
                    high_severity,
                    table.location(plan.item, row),
                    actual=value,
                    related=tuple(table.location(plan.item, r) for r in witnesses),
                    context={
                        "related_location_count": count,
                        "related_locations_truncated": count > len(witnesses),
                        "total": total,
                        "group_count": count,
                        "canonical_identity": group,
                        "display_identity": display,
                    },
                )
        if run.collector.stopped:
            return
        if round(value, 4) < low_limit:
            run.emit(
                run.message(low_message, **display, occupancy=display_number(value)),
                low_severity,
                table.location(plan.item, row),
                actual=value,
                context={"occupancy": value, "display_identity": display},
            )


def wavelength_kind(value):
    # Scientific exponents may contain '-' and must not be mistaken for ranges.
    scalar = r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"

    def positive(token):
        return bool(re.fullmatch(scalar, token.strip())) and 0 < float(token) < math.inf

    if positive(value):
        return "single"
    pieces = value.split(",")
    if len(pieces) >= 2 and all(positive(p) for p in pieces):
        return "multi"
    match = re.fullmatch(rf"\s*({scalar})\s*-\s*({scalar})\s*", value)
    if match and positive(match[1]) and positive(match[2]) and float(match[1]) <= float(match[2]):
        return "multi"
    return "invalid"


def wavelength(plan, run):
    source, target = run.table(plan.source), run.table(plan.target)
    if source is None or target is None:
        run.skip("category_absent", not_applicable=True)
        return
    index = run.join(target, plan.target_key)
    for row in run.rows(source):
        source_key = key(source.raw(plan.source_key, row))
        if source_key is None:
            run.skip("join_key_unfilled")
            continue
        matches = index.get(source_key, [])
        protocols = {}
        for r in reversed(matches):
            protocol = text(target.raw(plan.target, r))
            if protocol is not None:
                protocols[protocol.upper()] = r
        raw = source.raw(plan.source, row)
        value = text(raw)
        if raw in (None, ".", "?"):
            run.skip("wavelength_null")
            continue
        branch = next((b for b in plan.empty_branches if value is None and b.protocol in protocols), None)
        kind = wavelength_kind(value) if value is not None else "empty"
        if branch is None:
            branch = next((b for b in plan.branches if b.protocol in protocols and b.requires != kind), None)
        if branch is not None:
            target_row = protocols[branch.protocol]
            run.emit(
                branch.message,
                branch.severity,
                source.location(plan.source, row),
                related=(target.location(plan.target, target_row),),
                actual=value,
                context={
                    "join_key": source_key,
                    "wavelength_kind": kind,
                    "invalid_wavelength_syntax": kind == "invalid",
                    "target_count": len(matches),
                    "branch": branch.ordinal,
                },
            )


def accession(plan, run, table):
    for row in run.rows(table):
        value = text(table.raw(plan.item, row))
        if value is None:
            run.skip("optional_target_unfilled")
            continue
        value = value.strip()
        driver = text(table.raw(plan.driver, row)) if plan.driver else None
        for branch in plan.branches:
            if plan.kind == "accession_format_rule":
                if driver is None or driver.strip().upper() != branch.driver:
                    continue
            elif not conditions_match(table, row, branch.conditions, folded=True):
                continue
            valid = False
            if plan.kind == "conditional_value_rule":
                valid = value.casefold() in {v.strip().casefold() for v in branch.values}
            else:
                for name in branch.formats:
                    fmt = run.formats[name]
                    normalized = (
                        value.lower()
                        if fmt.normalization == "lower"
                        else (value.upper() if fmt.normalization == "upper" else value)
                    )
                    valid |= getattr(fmt.pattern, fmt.match_mode)(normalized) is not None
            if not valid:
                run.emit(
                    branch.message,
                    branch.severity,
                    table.location(plan.item, row),
                    actual=value,
                    context={"branch": branch.ordinal},
                )
                break


def sequence_predicate(plan, run, table):
    for row in run.rows(table):
        value = text(table.raw(plan.item, row))
        if value is None:
            continue
        sequence = value.translate(str.maketrans("", "", " \t\r\n")).upper()
        for branch in plan.branches:
            matches = (
                bool(sequence) and set(sequence) == {"A"}
                if branch.predicate == "homopolymer_ala"
                else (branch.substring.upper() in sequence)
            )
            if matches:
                run.emit(
                    branch.message, branch.severity, table.location(plan.item, row), context={"branch": branch.ordinal}
                )
                break


def sequence_model(plan, run):
    sequence, atoms = run.view.table("entity_poly_seq"), run.view.table("atom_site")
    if sequence is None or atoms is None:
        run.skip("polymer_sequence_or_atoms_absent", not_applicable=True)
        return
    allowed = defaultdict(set)
    for row in run.rows(sequence):
        entity, position, component = (key(sequence.raw(i, row)) for i in ("entity_id", "num", "mon_id"))
        if entity is None or position is None or component is None:
            continue
        try:
            number_id = int(position)
        except ValueError:
            run.unusable(sequence, "num", row, "sequence_position_invalid")
            continue
        allowed[(entity, number_id)].add(component.upper())
    sequence_entities = {entity for entity, _ in allowed}
    sequence_by_entity = defaultdict(list)
    for (entity, position), components in allowed.items():
        sequence_by_entity[entity].append((position, components))
    for positions in sequence_by_entity.values():
        positions.sort()
    excluded = set()
    entities = run.view.table("entity")
    if entities:
        excluded = {
            key(entities.raw("id", r))
            for r in range(entities.row_count)
            if text(entities.raw("type", r)) in ("non-polymer", "water")
        }
    chain_entities = {}
    asym = run.view.table("struct_asym")
    if asym:
        chain_entities = {key(asym.raw("id", r)): key(asym.raw("entity_id", r)) for r in range(asym.row_count)}
    residues = {}
    for row in run.rows(atoms):
        label_chain = key(atoms.raw("label_asym_id", row))
        author_chain = key(atoms.raw("auth_asym_id", row))
        entity = key(atoms.raw("label_entity_id", row)) or chain_entities.get(label_chain)
        if entity in excluded:
            continue
        label = key(atoms.raw("label_seq_id", row))
        auth = key(atoms.raw("auth_seq_id", row))
        insert = key(atoms.raw("pdbx_PDB_ins_code", row)) or ""
        component = key(atoms.raw("label_comp_id", row)) or key(atoms.raw("auth_comp_id", row))
        chain = label_chain or (author_chain, entity)
        model = integer_identity(key(atoms.raw("pdbx_PDB_model_num", row)) or "1")
        if model is None:
            run.unusable(atoms, "pdbx_PDB_model_num", row, "model_number_invalid")
            continue
        if entity is None or component is None or not (label_chain or author_chain) or not (label or auth):
            run.unusable(atoms, "label_seq_id", row, "sequence_identity_unavailable")
            continue
        if entity not in sequence_entities:
            run.skip("entity_sequence_absent")
            continue
        try:
            label_num = int(label) if label is not None else None
        except ValueError:
            run.unusable(atoms, "label_seq_id", row, "label_sequence_number_invalid")
            continue
        residue_key = (model, chain, entity, label_num if label_num is not None else ("auth", auth, insert))
        record = residues.setdefault(
            residue_key,
            {
                "entity": entity,
                "label": label_num,
                "auth": auth,
                "insert": insert,
                "chain": author_chain or label_chain,
                "components": {},
                "first": row,
            },
        )
        component = component.upper()
        atom = key(atoms.raw("label_atom_id", row)) or key(atoms.raw("auth_atom_id", row))
        anchor_priority = 0 if atom in ("CA", "C1'", "P") else 1
        prior = record["components"].get(component)
        if prior is None or anchor_priority < prior[0]:
            record["components"][component] = (anchor_priority, row)
    by_chain = defaultdict(list)
    for residue_key, record in residues.items():
        by_chain[residue_key[:3]].append(record)
    cells = 0
    for (_, _, entity), records in by_chain.items():
        positions = sequence_by_entity[entity]
        position_index = {pos: i for i, (pos, _) in enumerate(positions)}

        def finding(record, expected, method, context):
            for component, (_, row) in record["components"].items():
                if expected is not None and component in expected:
                    continue
                message = plan.missing_message if expected is None else plan.mismatch_message
                display = {
                    "chain": record["chain"],
                    "residue": (record["auth"] or str(record["label"])) + record["insert"],
                    "comp": component,
                    "sequence_comp": "/".join(sorted(expected or ())),
                }
                run.emit(
                    run.message(message, **display),
                    plan.severity,
                    atoms.location("label_comp_id", row),
                    context={"mapping_method": method, "allowed_components": tuple(sorted(expected or ())), **context},
                )

        for record in records:
            if record["label"] is not None:
                finding(record, allowed.get((entity, record["label"])), "label", {})
        if all(r["label"] is not None for r in records):
            continue

        def order(record):
            try:
                return (0, int(record["auth"]), record["insert"])
            except (TypeError, ValueError):
                return (1, record["first"], "")

        records.sort(key=order)
        max_offset = max(0, len(positions) - len(records))
        anchors = [
            (i, position_index.get(record["label"])) for i, record in enumerate(records) if record["label"] is not None
        ]
        if anchors:
            candidates = {position - i for i, position in anchors if position is not None}
            offsets = (
                [next(iter(candidates))]
                if len(candidates) == 1
                and all(position is not None for _, position in anchors)
                and 0 <= next(iter(candidates)) <= max_offset
                else []
            )
            offset_count = len(offsets)
        else:
            offset_count = max_offset + 1
            offsets = range(offset_count)
        required = offset_count * len(records)
        if cells + required > run.collector.options.max_alignment_cells:
            run.skip("alignment_budget_exhausted", blocked=True)
            run.emit(
                "Sequence mapping comparison budget exhausted",
                CheckSeverity.FATAL,
                atoms.location(),
                code="SEQUENCE_MAPPING_LIMIT_REACHED",
                check_type="engine",
            )
            continue
        if not offsets:
            for record in records:
                if record["label"] is None:
                    run.unusable(atoms, "label_seq_id", record["first"], "sequence_anchor_conflict")
            continue
        best, selected_offset, ties = -1, offsets[0], 0
        for offset in offsets:
            if not run.collector.poll():
                return
            score = 0
            for i, record in enumerate(records):
                if cells % 4096 == 0 and not run.collector.poll():
                    return
                score += offset + i < len(positions) and bool(set(record["components"]) & positions[offset + i][1])
                cells += 1
            if score > best:
                best, selected_offset, ties = score, offset, 1
            elif score == best:
                ties += 1
        for i, record in enumerate(records):
            if record["label"] is None:
                expected = positions[selected_offset + i][1] if selected_offset + i < len(positions) else None
                finding(
                    record,
                    expected,
                    "window_heuristic",
                    {"score": best, "tie_count": ties, "candidate_range": (offsets[0], offsets[-1])},
                )
    run.record["alignment_cells"] = cells


def procedural(plan, run):
    if plan.kind == "wavelength_protocol_consistency":
        wavelength(plan, run)
        return
    if plan.kind == "sequence_model_mismatch":
        sequence_model(plan, run)
        return
    table = run.view.table(plan.category)
    if table is None:
        run.skip("category_absent", not_applicable=True)
    elif plan.kind == "atom_site_occupancy":
        occupancy(plan, run, table)
    elif plan.kind == "sequence_predicate_warning":
        sequence_predicate(plan, run, table)
    else:
        accession(plan, run, table)
