"""Stage Surreal data and import it into a tenant (E1 + E2 gate command).

Two read paths, one mapping contract:

* ``--input <dir>``  replay an existing ``planinc-export-v1`` tree;
* ``--surreal-url``  read a live SurrealDB (SurrealKV-backed) through its SQL
  endpoint and stage it into that same tree first (``surreal_source.py``).

Both paths validate the export, write a resumable checkpoint, and — without
``--dry-run`` — run ``import_surreal_workspace`` for the real tenant import.
``--uploads-root`` plus ``--media-root`` additionally copy the indexed
attachment bytes into ``<media-root>/<tenant>/…`` (``attachments.py``).
"""

import contextlib
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy import attachments, surreal_source


class Command(BaseCommand):
    help = (
        "Stage a Surreal export (or a live SurrealDB read) and import it into "
        "a tenant workspace, with attachment byte copy."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=Path,
            default=None,
            help="Existing planinc-export-v1 directory.",
        )
        parser.add_argument(
            "--surreal-url",
            default=None,
            help="Live Surreal SQL endpoint (e.g. http://127.0.0.1:8000); "
            "tables are read and staged before the import.",
        )
        parser.add_argument("--namespace", default="planinc")
        parser.add_argument("--database", default="planinc")
        parser.add_argument("--username", default=None)
        parser.add_argument("--password", default=None)
        parser.add_argument(
            "--tables",
            default=",".join(surreal_source.DEFAULT_TABLES),
            help="Comma-separated Surreal tables to read (default: accounts,notes).",
        )
        parser.add_argument(
            "--stage-dir",
            type=Path,
            default=None,
            help="Where a live read is staged (default: a new temp directory).",
        )
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--workspace", default="personal")
        parser.add_argument("--checkpoint", type=Path, default=None)
        parser.add_argument("--report", type=Path, default=None)
        parser.add_argument(
            "--uploads-root",
            type=Path,
            default=None,
            help="Source directory the export's uploads/index.jsonl paths "
            "are relative to.",
        )
        parser.add_argument(
            "--media-root",
            type=Path,
            default=None,
            help="Destination root for tenant-prefixed attachment bytes.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["input"] is None and not options["surreal_url"]:
            raise CommandError(
                "Provide --input (an existing export) or --surreal-url "
                "(a live SurrealDB)."
            )
        if bool(options["uploads_root"]) != bool(options["media_root"]):
            raise CommandError(
                "--uploads-root and --media-root must be given together "
                "to copy attachments."
            )

        with self._root(options) as root:
            manifest = self._manifest(root)
            tables = manifest.get("records", {})
            indexed = self._indexed_paths(root)
            checkpoint = {
                "tenant": options["tenant"],
                "workspace": options["workspace"],
                "format": manifest["format"],
                "dry_run": bool(options["dry_run"]),
                "source": manifest.get("source", str(root)),
                "completed_tables": sorted(tables),
                "record_counts": tables,
                "unmapped_tables": sorted(
                    set(tables) - set(surreal_source.SURREAL_TABLE_MODEL_MAP)
                ),
                "mapping": manifest.get(
                    "mapping", surreal_source.SURREAL_TABLE_MODEL_MAP
                ),
                "files": manifest.get("files", {"count": len(indexed), "bytes": 0}),
                "indexed_files": len(indexed),
            }

            if options["dry_run"]:
                self._write(options["checkpoint"], checkpoint)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Validated {len(tables)} tables for tenant "
                        f"{options['tenant']} (dry run)."
                    )
                )
                return

            call_command(
                "import_surreal_workspace",
                input=root,
                tenant=options["tenant"],
                workspace=options["workspace"],
                report=options["report"],
                dry_run=False,
            )

            if options["uploads_root"] and options["media_root"]:
                summary = attachments.copy_indexed_uploads(
                    root / "uploads" / "index.jsonl",
                    options["uploads_root"],
                    options["media_root"],
                    options["tenant"],
                )
                checkpoint["attachments"] = summary
                self._merge_report(options["report"], {"attachments": summary})
                self.stdout.write(
                    f"Attachments: {summary['copied']} copied, "
                    f"{len(summary['missing'])} missing, "
                    f"{len(summary['mismatched'])} mismatched, "
                    f"{len(summary['unsafe'])} unsafe."
                )

            self._write(options["checkpoint"], checkpoint)

    @contextlib.contextmanager
    def _root(self, options):
        """Yield the export directory, staging a live Surreal read first."""
        if options["input"] is not None:
            yield options["input"]
            return

        tables = [
            table.strip() for table in options["tables"].split(",") if table.strip()
        ]
        if not tables:
            raise CommandError("--tables must name at least one table.")
        destination = options["stage_dir"] or Path(
            tempfile.mkdtemp(prefix="planinc-surreal-stage-")
        )
        try:
            surreal_source.stage_from_surreal(
                destination,
                url=options["surreal_url"],
                namespace=options["namespace"],
                database=options["database"],
                tables=tables,
                username=options["username"],
                password=options["password"],
            )
        except surreal_source.SurrealReadError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Staged Surreal tables in {destination}")
        yield destination

    @staticmethod
    def _manifest(root: Path) -> dict:
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise CommandError(f"Missing export manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != surreal_source.FORMAT:
            raise CommandError("Unsupported PlanInc export format.")
        return manifest

    @staticmethod
    def _indexed_paths(root: Path) -> list[str]:
        index_path = root / "uploads" / "index.jsonl"
        if not index_path.is_file():
            raise CommandError(f"Missing uploads index: {index_path}")
        lines = index_path.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if line.strip()]

    @staticmethod
    def _write(path, payload) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _merge_report(path, extra: dict) -> None:
        if path is None or not Path(path).is_file():
            return
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        report.update(extra)
        Path(path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
