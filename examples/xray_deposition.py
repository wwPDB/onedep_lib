"""
Example: X-ray deposition using the nextdep_dsp public API.

Follows the sequence diagram in docs/deposit.mermaid:
  1. deposit_init()
  2. set_experiment_type()
  3. check_required_files()
  4. Per-file checks (mmCIF + file-type)
  5. add_file() for each file
  6. deposit()   — triggers upload + process(), returns dep_id
  7. get_status() — poll until done (or just print the dep_id)
"""

from __future__ import annotations

import logging
import time

import onedep_lib as dsp
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logging.disable(logging.ERROR)

_console = Console(stderr=True)

# ── Configuration ─────────────────────────────────────────────────────────────
# Change all values marked with  <<<< CHANGE THIS  before running.

EMAIL = "your.email@example.com"  # <<<< CHANGE THIS
USERS = ["0000-0000-0000-0000"]  # <<<< CHANGE THIS  (ORCID iD)
COORD_FILE = "/path/to/your/coord.cif"  # <<<< CHANGE THIS
SF_FILE = "/path/to/your/sf.cif"  # <<<< CHANGE THIS


def ok(msg: str) -> None:
    _console.print(f"[bold green]✓[/bold green] {msg}")


def fail(msg: str) -> None:
    _console.print(f"[bold red]✗[/bold red] {msg}")


def print_report(label: str, report: dsp.CheckReport) -> None:
    if report.ok:
        ok(label)
    else:
        fail(label)
        for issue in report.issues:
            _console.print(f"  [yellow]{issue.severity.value.upper()}[/yellow] [{issue.code}] {issue.message}")


def main() -> None:
    # ── 0. Validate configuration ─────────────────────────────────────────────
    _unset = [
        name
        for name, placeholder, value in [
            ("EMAIL", "your.email@example.com", EMAIL),
            ("USERS", ["0000-0000-0000-0000"], USERS),
            ("COORD_FILE", "/path/to/your/coord.cif", COORD_FILE),
            ("SF_FILE", "/path/to/your/sf.cif", SF_FILE),
        ]
        if value == placeholder
    ]
    if _unset:
        msg = Text()
        msg.append("Placeholder values not changed:\n", style="bold yellow")
        for name in _unset:
            msg.append(f"  • {name}\n", style="yellow")
        msg.append("\nEdit the constants at the top of this file before running.", style="dim")
        _console.print(Panel(msg, title="[bold red]⚠  Configuration[/bold red]", border_style="red"))

    with _console.status("[cyan]Initializing deposit…[/cyan]", spinner="dots") as spin:
        # ── 1. Initialization ────────────────────────────────────────────────
        dep = dsp.deposit_init(email=EMAIL, users=USERS, country=dsp.Country.USA)
        ok(f"Deposit initialized  session_id={dep.session_id}")

        # ── 2. Set experiment type ────────────────────────────────────────────
        spin.update("[cyan]Setting experiment type…[/cyan]")
        dep.set_experiment_type(dsp.ExperimentType.XRAY)
        ok(f"Experiment type set  [{dsp.ExperimentType.XRAY.value}]")

        # ── 3. Check auth key ─────────────────────────────────────────────────
        spin.update("[cyan]Checking auth key…[/cyan]")
        auth_ok = dep.check_auth_key()
        if auth_ok:
            ok("Auth key valid")
        else:
            fail("Auth key invalid")

        # ── 4. Pre-add required files check ───────────────────────────────────
        spin.update("[cyan]Checking required files (pre-add)…[/cyan]")
        report = dep.check_required_files()
        print_report("Required files check (empty session)", report)

        # ── 5. Add files ──────────────────────────────────────────────────────
        spin.update("[cyan]Adding coordinate file…[/cyan]")
        coord_id = dep.add_file(COORD_FILE, dsp.FileType.MMCIF_COORD)
        ok(f"Added coord file  file_id={coord_id}  type={dsp.FileType.MMCIF_COORD.value}")

        spin.update("[cyan]Adding structure factors file…[/cyan]")
        sf_id = dep.add_file(SF_FILE, dsp.FileType.CRYSTAL_STRUC_FACTORS)
        ok(f"Added SF file  file_id={sf_id}  type={dsp.FileType.CRYSTAL_STRUC_FACTORS.value}")

        # ── 6. File checks ────────────────────────────────────────────────────
        spin.update("[cyan]Running file checks…[/cyan]")
        print_report("check_mmcif_file (coord)", dep.check_mmcif_file(coord_id))
        print_report(
            "check_mmcif_category (coord, _atom_site)",
            dep.check_mmcif_category(coord_id, "_atom_site"),
        )
        print_report(
            "check_mmcif_field (coord, _atom_site, id)",
            dep.check_mmcif_field(coord_id, "_atom_site", "id"),
        )
        print_report(
            "check_file_type (coord, MMCIF_COORD)",
            dep.check_file_type(coord_id, dsp.FileType.MMCIF_COORD),
        )
        print_report("check_mmcif_file (sf)", dep.check_mmcif_file(sf_id))
        print_report(
            "check_file_type (sf, CRYSTAL_STRUC_FACTORS)",
            dep.check_file_type(sf_id, dsp.FileType.CRYSTAL_STRUC_FACTORS),
        )

        # ── 7. Post-add required files check ──────────────────────────────────
        spin.update("[cyan]Checking required files (post-add)…[/cyan]")
        report = dep.check_required_files()
        print_report("Required files check (with files)", report)

        if not report.ok:
            fail("Aborting: required files check failed.")
            return

        # # ── 8. Deposit ────────────────────────────────────────────────────────
        spin.update("[cyan]Submitting deposit…[/cyan]")
        try:
            dep_id = dep.deposit()
            ok(f"Deposit submitted  dep_id={dep_id}")
        except (RuntimeError, dsp.DepositApiException) as exc:
            fail(f"deposit() failed: {exc}")
            return

        # ── 9. Poll status ────────────────────────────────────────────────────
        for _ in range(1, 64):
            try:
                status = dep.get_status()
                spin.update(
                    f"[cyan]{status.details if isinstance(status, dsp.DepositStatus) else status.message}[/cyan]"
                )
                if isinstance(status, dsp.DepositStatus) and status.status.lower() == "finished":
                    ok(f"Processing finished  dep_id={dep_id}")
                    break
            except Exception as exc:
                fail(f"get_status() failed: {exc}")
            time.sleep(5)

    _console.print("\n[bold]Done.[/bold] Log in to the DepUI to complete your submission.")


if __name__ == "__main__":
    main()
