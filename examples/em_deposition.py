"""
Example: EM (Single Particle Analysis) deposition using the nextdep_dsp public API.

Follows the sequence diagram in docs/deposit.mermaid:
  1. deposit_init()
  2. set_experiment_type()  — EM
  3. check_required_files()
  4. Per-file checks
  5. add_file() for each file
  6. deposit()   — triggers upload + process(), returns dep_id
  7. get_status() — poll until done
"""

from __future__ import annotations

import logging
import time
import os

import onedep_lib as dsp
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from onedep_lib.config import DepositConfig

logging.disable(logging.ERROR)

_console = Console(stderr=True)

# ── Configuration ─────────────────────────────────────────────────────────────
# Change all values marked with  <<<< CHANGE THIS  before running.

EMAIL = os.getenv("WWPDB_EMAIL") or "your.email@example.com"  # <<<< CHANGE THIS
USERS = os.getenv("WWPDB_USERS") and os.getenv("WWPDB_USERS").split(",") or ["0000-0000-0000-0000"]  # <<<< CHANGE THIS  (ORCID iD)

BASE = os.getenv("WWPDB_EM_BASE") or "/path/to/your/em/files"  # <<<< CHANGE THIS  (directory containing your EM files)

COORD_FILE = f"{BASE}/coord.cif"  # <<<< CHANGE THIS  (adjust filename)
MAP_FILE = f"{BASE}/primary.map.gz"  # <<<< CHANGE THIS  (adjust filename)
HALF_MAP_1 = f"{BASE}/half_map_1.map.gz"  # <<<< CHANGE THIS  (adjust filename)
HALF_MAP_2 = f"{BASE}/half_map_2.map.gz"  # <<<< CHANGE THIS  (adjust filename)
IMAGE_FILE = f"{BASE}/image.png"  # <<<< CHANGE THIS  (adjust filename)
# FSC_XML_FILE = f"{BASE}/fsc.xml"


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
    config = DepositConfig.load()

    # ── 0. Validate configuration ─────────────────────────────────────────────
    _unset = [
        name
        for name, placeholder, value in [
            ("EMAIL", "your.email@example.com", EMAIL),
            ("USERS", ["0000-0000-0000-0000"], USERS),
            ("BASE", "/path/to/your/em/files", BASE),
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
        dep = dsp.deposit_init(email=EMAIL, users=USERS, country=dsp.Country.USA, config=config)
        ok(f"Deposit initialized  session_id={dep.session_id}")

        # ── 2. Set experiment type and EM-specific params ─────────────────────
        spin.update("[cyan]Setting experiment type…[/cyan]")
        dep.set_experiment_type(dsp.ExperimentType.EM)
        dep.set_em_params(em_subtype=dsp.EMSubType.SPA, coordinates=True)
        ok(f"Experiment type set  [{dsp.ExperimentType.EM.value}]  subtype={dsp.EMSubType.SPA.value}  coordinates=True")

        # ── 3. Check auth key ─────────────────────────────────────────────────
        spin.update("[cyan]Checking auth key…[/cyan]")
        auth_ok = dsp.check_auth_key(config=config)
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
        ok(f"Added coord    file_id={coord_id}  type={dsp.FileType.MMCIF_COORD.value}")

        spin.update("[cyan]Adding primary map…[/cyan]")
        map_id = dep.add_file(MAP_FILE, dsp.FileType.EM_MAP)
        ok(f"Added map      file_id={map_id}  type={dsp.FileType.EM_MAP.value}")

        spin.update("[cyan]Adding half map 1…[/cyan]")
        half1_id = dep.add_file(HALF_MAP_1, dsp.FileType.EM_HALF_MAP)
        ok(f"Added half1    file_id={half1_id}  type={dsp.FileType.EM_HALF_MAP.value}")

        spin.update("[cyan]Adding half map 2…[/cyan]")
        half2_id = dep.add_file(HALF_MAP_2, dsp.FileType.EM_HALF_MAP)
        ok(f"Added half2    file_id={half2_id}  type={dsp.FileType.EM_HALF_MAP.value}")

        spin.update("[cyan]Adding image file…[/cyan]")
        image_id = dep.add_file(IMAGE_FILE, dsp.FileType.ENTRY_IMAGE)
        ok(f"Added image    file_id={image_id}  type={dsp.FileType.ENTRY_IMAGE.value}")
        # fsc_xml_id = dep.add_file(FSC_XML_FILE, dsp.FileType.FSC_XML)
        # ok(f"Added fsc_xml  file_id={fsc_xml_id}  type={dsp.FileType.FSC_XML.value}")

        # ── 5b. Set voxel values for map files ────────────────────────────────
        spin.update("[cyan]Setting voxel values…[/cyan]")
        dep.set_voxel_values(map_id, spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
        dep.set_voxel_values(half1_id, spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
        dep.set_voxel_values(half2_id, spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
        ok("Voxel values set for map, half1, half2")

        # ── 6. File checks ────────────────────────────────────────────────────
        spin.update("[cyan]Running file checks…[/cyan]")
        print_report("check_mmcif_file (coord)", dep.check_mmcif_file(coord_id))
        print_report(
            "check_mmcif_category (coord, _atom_site)",
            dep.check_mmcif_category(coord_id, "_atom_site"),
        )
        print_report(
            "check_file_type (coord, MMCIF_COORD)",
            dep.check_file_type(coord_id, dsp.FileType.MMCIF_COORD),
        )
        print_report("check_file_type (map,   EM_MAP)", dep.check_file_type(map_id, dsp.FileType.EM_MAP))
        print_report("check_file_type (half1, EM_HALF_MAP)", dep.check_file_type(half1_id, dsp.FileType.EM_HALF_MAP))
        print_report("check_file_type (half2, EM_HALF_MAP)", dep.check_file_type(half2_id, dsp.FileType.EM_HALF_MAP))
        print_report("check_file_type (image, ENTRY_IMAGE)", dep.check_file_type(image_id, dsp.FileType.ENTRY_IMAGE))
        # print_report("check_file_type (fsc,   FSC_XML)",     dep.check_file_type(fsc_xml_id, dsp.FileType.FSC_XML))

        # ── 7. Post-add required files check ──────────────────────────────────
        spin.update("[cyan]Checking required files (post-add)…[/cyan]")
        report = dep.check_required_files()
        print_report("Required files check (with files)", report)

        if not report.ok:
            fail("Aborting: required files check failed.")
            return

        # ── 8. Deposit ────────────────────────────────────────────────────────
        spin.update("[cyan]Submitting deposit…[/cyan]")
        try:
            dep_id = dep.deposit()
            ok(f"Deposit submitted  dep_id={dep_id} url: {dep.site_url}")
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
