"""Projected views preserving physical mmCIF table and row identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import gemmi

from onedep_lib.checks.report import CifLocation


def tag(category: str, item: str) -> str:
    return f"_{category.lstrip('_').rstrip('.')}.{item}"


@dataclass
class Table:
    category: str
    table_index: int
    block_index: int
    block_name: str
    line: int | None
    loop: gemmi.cif.Loop | None = None
    columns: dict[str, int] = field(default_factory=dict)
    pairs: dict[str, str] = field(default_factory=dict)
    # True while every tag seen for this table was spelled exactly as its canonical lowercase
    # form (the convention real PDBx dictionaries and files both follow). Gemmi's native parent
    # checker matches dictionary-declared tag spelling case-sensitively against the input's
    # original spelling, so an exact-case table is one the native backend can be trusted to
    # check correctly; a mixed-case table needs the Python foreign-key supplement instead.
    exact_case: bool = True
    _canonical_cache: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @property
    def row_count(self) -> int:
        return self.loop.length() if self.loop is not None else 1

    def _canonical(self, item: str) -> str:
        # item names are reused across every row of a table (often million-row
        # loops), so memoize the lower()/format() work instead of repeating it
        # per call.
        canonical = self._canonical_cache.get(item)
        if canonical is None:
            canonical = item.lower() if item.startswith("_") else tag(self.category, item).lower()
            self._canonical_cache[item] = canonical
        return canonical

    def raw(self, item: str, row: int) -> str | None:
        canonical = self._canonical(item)
        if self.loop is not None:
            col = self.columns.get(canonical)
            return self.loop[row, col] if col is not None else None
        return self.pairs.get(canonical)

    def has(self, item: str) -> bool:
        canonical = self._canonical(item)
        return canonical in self.columns or canonical in self.pairs

    def location(self, item: str | None = None, row: int | None = None) -> CifLocation:
        canonical = item if item is None or item.startswith("_") else tag(self.category, item)
        return CifLocation(
            self.block_name,
            self.category,
            canonical,
            row,
            self.line,
            None,
            self.block_index,
            self.table_index,
            self.columns.get(canonical.lower()) if canonical else None,
        )

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(self.columns if self.loop is not None else self.pairs)


class BlockView:
    def __init__(self, block: gemmi.cif.Block, index: int):
        self.block = block
        self.index = index
        self.tables: dict[str, Table] = {}
        self.errors: list[tuple[str, str, CifLocation]] = []
        self.indexes: dict[tuple, Any] = {}
        self.index_builds = 0
        for table_index, item in enumerate(block):
            if item.frame is not None:
                self.errors.append(
                    (
                        "CIF_UNSUPPORTED_SAVE_FRAME",
                        "Input save frames are unsupported",
                        CifLocation(block.name, block_index=index, table_index=table_index),
                    )
                )
                continue
            if item.loop is not None:
                loop = item.loop
                categories = {t.lower().split(".")[0].lstrip("_") for t in loop.tags}
                if len(categories) != 1 or any("." not in t for t in loop.tags):
                    self.errors.append(
                        (
                            "CIF_TABLE_LAYOUT",
                            "A loop must contain one mmCIF category",
                            CifLocation(block.name, block_index=index, table_index=table_index),
                        )
                    )
                    continue
                category = next(iter(categories))
                table = Table(
                    category,
                    table_index,
                    index,
                    block.name,
                    item.line_number,
                    loop,
                    {t.lower(): c for c, t in enumerate(loop.tags)},
                    exact_case=all(t == t.lower() for t in loop.tags),
                )
                self._add(category, table)
            elif item.pair is not None:
                name, raw = item.pair
                if "." not in name:
                    self.errors.append(
                        (
                            "CIF_TABLE_LAYOUT",
                            "Input tags must use mmCIF category.item names",
                            CifLocation(block.name, item=name, block_index=index),
                        )
                    )
                    continue
                category = name.lower().split(".")[0].lstrip("_")
                table = self.tables.get(category)
                if table is None:
                    table = Table(category, table_index, index, block.name, item.line_number)
                    self.tables[category] = table
                if table.loop is not None:
                    self.errors.append(
                        ("CIF_TABLE_LAYOUT", "Category is split across incompatible tables", table.location(name))
                    )
                else:
                    table.pairs[name.lower()] = raw
                    table.exact_case = table.exact_case and name == name.lower()

    def _add(self, category: str, table: Table):
        if category in self.tables:
            self.errors.append(("CIF_TABLE_LAYOUT", "Category is split across incompatible tables", table.location()))
        else:
            self.tables[category] = table

    def table(self, category: str) -> Table | None:
        return self.tables.get(category.lower().lstrip("_").rstrip("."))
