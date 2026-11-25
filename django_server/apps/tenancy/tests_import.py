"""Tests for the Surreal→workspace importer (idempotent, LWW, reported)."""

import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.notes.models import Note
from apps.workspaces.models import Workspace, WorkspaceMember

from .models import Membership, Tenant


def _write_export(root, accounts, notes):
    records = root / "records"
    records.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "planinc-export-v1",
                "records": {"accounts": len(accounts), "notes": len(notes)},
            }
        ),
        encoding="utf-8",
    )
    with (records / "accounts.jsonl").open("w", encoding="utf-8") as handle:
        for row in accounts:
            handle.write(json.dumps(row) + "\n")
    with (records / "notes.jsonl").open("w", encoding="utf-8") as handle:
        for row in notes:
            handle.write(json.dumps(row) + "\n")


class ImportSurrealWorkspaceTests(TestCase):
    def setUp(self):
        call_command("provision_tenant", "demo", name="Demo")

    def test_import_creates_users_memberships_notes_and_report(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_export(
                root,
                accounts=[
                    {"id": 1, "name": "ada"},
                    {"id": 2, "name": ""},
                ],
                notes=[
                    {
                        "id": 10,
                        "accountId": 1,
                        "content": "# Shopping\n- milk",
                        "updatedAt": "2026-01-02T00:00:00Z",
                    },
                ],
            )
            report = root / "report.json"
            call_command(
                "import_surreal_workspace",
                input=root,
                tenant="demo",
                workspace="personal",
                report=report,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        tenant = Tenant.objects.get(slug="demo")
        self.assertEqual(
            Membership.objects.filter(tenant=tenant).count(), 1
        )
        workspace = Workspace.objects.get(tenant=tenant, slug="personal")
        self.assertEqual(
            WorkspaceMember.objects.filter(workspace=workspace).count(), 1
        )
        note = Note.objects.get(tenant=tenant, external_id="surreal:notes:10")
        self.assertEqual(note.title, "Shopping")
        # Surreal accountId 1 (ada) maps to the Django author.
        self.assertEqual(note.author.username, "ada")
        self.assertEqual(payload["notes"]["created"], 1)
        self.assertEqual(len(payload["users"]["rejected"]), 1)

    def test_rerun_is_idempotent_and_respects_last_writer_wins(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[
                    {
                        "id": 10,
                        "content": "v1",
                        "updatedAt": "2026-01-02T00:00:00Z",
                    },
                ],
            )
            call_command(
                "import_surreal_workspace",
                input=root,
                tenant="demo",
                workspace="personal",
            )
            # Same export again: everything stale, nothing duplicated.
            call_command(
                "import_surreal_workspace",
                input=root,
                tenant="demo",
                workspace="personal",
            )
            tenant = Tenant.objects.get(slug="demo")
            self.assertEqual(
                Note.objects.filter(tenant=tenant).count(), 1
            )
            # Newer export wins.
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[
                    {
                        "id": 10,
                        "content": "v2",
                        "updatedAt": "2026-02-02T00:00:00Z",
                    },
                ],
            )
            call_command(
                "import_surreal_workspace",
                input=root,
                tenant="demo",
                workspace="personal",
            )
            note = Note.objects.get(
                tenant=tenant, external_id="surreal:notes:10"
            )
            self.assertEqual(note.body, "v2")

    def test_dry_run_writes_nothing(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[{"id": 10, "content": "v1"}],
            )
            call_command(
                "import_surreal_workspace",
                input=root,
                tenant="demo",
                workspace="personal",
                dry_run=True,
            )
        tenant = Tenant.objects.get(slug="demo")
        self.assertEqual(Note.objects.filter(tenant=tenant).count(), 0)
        self.assertFalse(
            Workspace.objects.filter(tenant=tenant).exists()
        )


class NativeSurrealImportTests(TestCase):
    """E1: a live Surreal read is staged and imported; attachments are copied."""

    def setUp(self):
        call_command("provision_tenant", "demo", name="Demo")

    @staticmethod
    def _fake_urlopen(tables):
        """Answer ``SELECT * FROM <table>;`` like SurrealDB's /sql endpoint."""
        import json as _json

        payloads = {
            table: [{"status": "OK", "time": "0s", "result": rows}]
            for table, rows in tables.items()
        }

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return _json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake(request, timeout=None):
            statement = request.data.decode()
            table = statement.split("FROM", 1)[1].strip().rstrip(";").strip()
            return _Response(payloads[table])

        return fake

    def test_live_read_stages_an_export_and_checkpoints(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        tables = {
            "accounts": [{"id": 1, "name": "ada"}],
            "notes": [
                {
                    "id": 10,
                    "accountId": 1,
                    "content": "# Shopping",
                    "updatedAt": "2026-01-02T00:00:00Z",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage = tmp_path / "stage"
            checkpoint = tmp_path / "checkpoint.json"
            with patch(
                "apps.tenancy.surreal_source.urlopen", self._fake_urlopen(tables)
            ):
                call_command(
                    "planinc_import_surreal",
                    surreal_url="http://surreal:8000",
                    tenant="demo",
                    checkpoint=checkpoint,
                    stage_dir=stage,
                    dry_run=True,
                )

            manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["format"], "planinc-export-v1")
            self.assertEqual(manifest["records"], {"accounts": 1, "notes": 1})
            self.assertTrue((stage / "checksums" / "sha256sums").is_file())
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["completed_tables"], ["accounts", "notes"])
            self.assertEqual(payload["unmapped_tables"], [])
            self.assertEqual(payload["indexed_files"], 0)

    def test_live_read_imports_into_the_tenant(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        tables = {
            "accounts": [{"id": 1, "name": "ada"}],
            "notes": [
                {
                    "id": 10,
                    "accountId": 1,
                    "content": "# Shopping\n- milk",
                    "updatedAt": "2026-01-02T00:00:00Z",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = tmp_path / "report.json"
            with patch(
                "apps.tenancy.surreal_source.urlopen", self._fake_urlopen(tables)
            ):
                call_command(
                    "planinc_import_surreal",
                    surreal_url="http://surreal:8000",
                    tenant="demo",
                    workspace="personal",
                    stage_dir=tmp_path / "stage",
                    report=report,
                )

            tenant = Tenant.objects.get(slug="demo")
            note = Note.objects.get(tenant=tenant, external_id="surreal:notes:10")
            self.assertEqual(note.title, "Shopping")
            self.assertEqual(note.author.username, "ada")
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["notes"]["created"], 1)

    def test_attachments_are_copied_under_the_tenant_prefix(self):
        import hashlib
        import tempfile
        from pathlib import Path

        payload = b"png-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "export"
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[{"id": 10, "content": "# Shopping"}],
            )
            (root / "uploads").mkdir(parents=True, exist_ok=True)
            uploads = tmp_path / "uploads"
            (uploads / "img").mkdir(parents=True)
            (uploads / "img" / "a.png").write_bytes(payload)
            index_entry = {
                "path": "img/a.png",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            (root / "uploads" / "index.jsonl").write_text(
                json.dumps(index_entry) + "\n", encoding="utf-8"
            )
            media = tmp_path / "media"
            checkpoint = tmp_path / "checkpoint.json"
            call_command(
                "planinc_import_surreal",
                input=root,
                tenant="demo",
                workspace="personal",
                checkpoint=checkpoint,
                uploads_root=uploads,
                media_root=media,
            )

            self.assertEqual((media / "demo" / "img" / "a.png").read_bytes(), payload)
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(state["attachments"]["copied"], 1)
            self.assertEqual(state["attachments"]["bytes"], len(payload))

    def test_unsafe_attachment_paths_are_rejected(self):
        import hashlib
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "export"
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[{"id": 10, "content": "# Shopping"}],
            )
            (root / "uploads").mkdir(parents=True, exist_ok=True)
            uploads = tmp_path / "uploads"
            uploads.mkdir()
            (uploads / "escape.txt").write_bytes(b"x")
            index_entry = {
                "path": "../escape.txt",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
            (root / "uploads" / "index.jsonl").write_text(
                json.dumps(index_entry) + "\n", encoding="utf-8"
            )
            media = tmp_path / "media"
            checkpoint = tmp_path / "checkpoint.json"
            call_command(
                "planinc_import_surreal",
                input=root,
                tenant="demo",
                workspace="personal",
                checkpoint=checkpoint,
                uploads_root=uploads,
                media_root=media,
            )

            self.assertFalse((tmp_path / "media" / "escape.txt").exists())
            self.assertFalse((media / "demo").exists())
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(state["attachments"]["unsafe"], ["../escape.txt"])
            self.assertEqual(state["attachments"]["copied"], 0)

    def test_uploads_root_requires_media_root(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "export"
            _write_export(
                root,
                accounts=[{"id": 1, "name": "ada"}],
                notes=[{"id": 10, "content": "# Shopping"}],
            )
            with self.assertRaises(CommandError):
                call_command(
                    "planinc_import_surreal",
                    input=root,
                    tenant="demo",
                    uploads_root=Path(tmp),
                )
