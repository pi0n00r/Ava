#!/usr/bin/env python3
# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Non-actuating tests for the Ava HTTP-advertisement activation transaction."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("ava_http_deploy", Path(__file__).with_name("deploy-ava-http-advertisement.py"))
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)


class FakeNative:
    def __init__(self):
        self.running = True; self.container_id = "fixture"; self.events = []
        self.counts = {"active_calls": 0, "active_sessions": 0, "asterisk_channels": 0}
        self.config_hash = "config-a"; self.logical = {"sha256": "a" * 64, "agent_count": 2, "columns": ["slug", "extra_json"]}
        self.database = b"SQLite format 3 fixture"
        self.fail_post_health = False

    def inspect(self):
        self.events.append("inspect")
        return {"id": self.container_id, "image": deploy.IMAGE, "running": self.running}

    def syntax(self, source): self.events.append("syntax"); compile(source, "engine.py", "exec")
    def installed_syntax(self): self.events.append("installed_syntax")
    def stop(self): self.events.append("stop"); self.running = False
    def start(self): self.events.append("start"); self.running = True
    def agent_snapshot(self, include_backup=False):
        self.events.append("snapshot_backup" if include_backup else "snapshot")
        return (copy.deepcopy(self.logical), self.database) if include_backup else copy.deepcopy(self.logical)
    def health(self):
        self.events.append("health")
        if self.fail_post_health and self.events.count("start"):
            self.config_hash = "changed"
        return {"status": "healthy", "config_hash": self.config_hash, **self.counts}
    def pbx_channels(self):
        self.events.append("pbx_channels")
        return {"authenticated": True, "operation": "GET /ari/channels", "channels": 0}


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ava-http-activate-test-"); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base / "live"; self.candidate = self.base / "candidate"; self.backups = self.base / "backups"
        self.root.mkdir(); self.candidate.mkdir(); self.native = FakeNative()
        (self.root / "engine.py").write_bytes(b"OLD = True\n"); (self.candidate / "engine.py").write_bytes(b"NEW = True\n")
        for path in (self.root / "engine.py", self.candidate / "engine.py"): path.chmod(0o644)
        owner = {"uid": os.geteuid(), "gid": os.getegid()}
        self.before = deploy.fingerprint(self.root / "engine.py")
        self.after = deploy.fingerprint(self.candidate / "engine.py")
        self.patches = [mock.patch.object(deploy, "BEFORE", deploy.stable(self.before)),
                        mock.patch.object(deploy, "AFTER", deploy.stable(self.after)),
                        mock.patch.object(deploy, "OWNER", owner), mock.patch.object(deploy, "BACKUP_BASE", self.backups)]
        for patch in self.patches: patch.start(); self.addCleanup(patch.stop)

    def apply(self): return deploy.apply(self.native, self.root, self.candidate, self.backups)

    def test_check_only_is_non_actuating(self):
        result = deploy.check(self.native, self.root, self.candidate)
        self.assertEqual(result["logical_agent_config"], self.native.logical)
        self.assertNotIn("stop", self.native.events); self.assertFalse(self.backups.exists())

    def test_exact_single_engine_apply_and_rollback_never_restore_database(self):
        backup, receipt = self.apply()
        self.assertEqual(deploy.stable(deploy.fingerprint(self.root / "engine.py")), deploy.AFTER)
        self.assertEqual(receipt["agents_db_backup"]["evidence_only"], True)
        self.assertEqual(receipt["agents_db_backup"]["restored"], False)
        self.assertEqual((backup / "agents-db-snapshot.sqlite").read_bytes(), self.native.database)
        deploy.rollback(self.native, backup, self.root)
        restored = deploy.fingerprint(self.root / "engine.py")
        self.assertEqual(deploy.stable(restored), deploy.stable(self.before))
        self.assertEqual(restored["mtime_ns"], self.before["mtime_ns"])
        self.assertEqual(self.native.database, b"SQLite format 3 fixture")

    def test_preimage_change_blocks_before_stop(self):
        (self.root / "engine.py").write_bytes(b"FOREIGN = True\n")
        with self.assertRaisesRegex(deploy.Blocked, "live_engine_preimage_mismatch"): self.apply()
        self.assertNotIn("stop", self.native.events)

    def test_candidate_change_blocks_before_stop(self):
        (self.candidate / "engine.py").write_bytes(b"FOREIGN = True\n")
        with self.assertRaisesRegex(deploy.Blocked, "candidate_engine_mismatch"): self.apply()
        self.assertNotIn("stop", self.native.events)

    def test_busy_health_blocks_before_stop(self):
        self.native.counts["active_calls"] = 1
        with self.assertRaisesRegex(deploy.Blocked, "quiescence_unavailable_or_active"): self.apply()
        self.assertNotIn("stop", self.native.events)

    def test_logical_config_change_before_stop_blocks(self):
        original = self.native.agent_snapshot; calls = [0]
        def changed(include_backup=False):
            value = original(include_backup); calls[0] += 1
            if calls[0] >= 3 and not include_backup:
                value["sha256"] = "b" * 64
            return value
        self.native.agent_snapshot = changed
        with self.assertRaisesRegex(deploy.Blocked, "pre_stop_state_changed"): self.apply()
        self.assertNotIn("stop", self.native.events)

    def test_postflight_logical_change_rolls_back_engine_only(self):
        original = self.native.agent_snapshot
        def changed(include_backup=False):
            value = original(include_backup)
            if self.native.events.count("start") and not include_backup: value["sha256"] = "b" * 64
            return value
        self.native.agent_snapshot = changed
        with self.assertRaises(deploy.Blocked): self.apply()
        self.assertEqual(deploy.stable(deploy.fingerprint(self.root / "engine.py")), deploy.stable(self.before))

    def test_postflight_config_hash_change_rolls_back(self):
        self.native.fail_post_health = True
        with self.assertRaises(deploy.Blocked): self.apply()
        self.assertEqual(deploy.stable(deploy.fingerprint(self.root / "engine.py")), deploy.stable(self.before))

    def test_external_postimage_change_prevents_rollback(self):
        backup, _ = self.apply(); (self.root / "engine.py").write_bytes(b"THIRD = True\n")
        with self.assertRaisesRegex(deploy.Blocked, "rollback_engine_conflict"):
            deploy.rollback(self.native, backup, self.root)

    def test_receipt_cannot_authorize_database_rewind(self):
        backup, _ = self.apply(); receipt_path = backup / "transaction.json"
        receipt = json.loads(receipt_path.read_bytes()); receipt["agents_db_backup"]["restored"] = True
        deploy.save_json(receipt_path, receipt)
        # A receipt can never turn the evidence backup into restorable state.
        with self.assertRaisesRegex(deploy.Blocked, "database_evidence_contract_mismatch"):
            deploy.rollback(self.native, backup, self.root)
        self.assertEqual(self.native.database, b"SQLite format 3 fixture")

    def test_container_identity_change_blocks(self):
        original = self.native.stop
        def stop(): original(); self.native.container_id = "successor"
        self.native.stop = stop
        with self.assertRaisesRegex(deploy.Blocked, "automatic_rollback_blocked"): self.apply()


class NativeContractTests(unittest.TestCase):
    def test_snapshot_code_is_readonly_and_backup_api_only(self):
        with mock.patch.object(deploy.Native, "run", return_value=json.dumps({"sha256": "a" * 64, "agent_count": 1, "columns": ["slug"]}).encode()) as run:
            deploy.Native().agent_snapshot()
        body = run.call_args.kwargs["body"].decode()
        self.assertIn("mode=ro", body); self.assertIn("c.backup(d)", body)
        self.assertNotIn("delete from", body.lower()); self.assertNotIn("update agents", body.lower())

    def test_health_and_control_surface_is_bounded(self):
        source = Path(deploy.__file__).read_text()
        self.assertIn("/health", source); self.assertNotIn("/reload", source)
        self.assertNotIn("/inference", source); self.assertNotIn("/tools", source)

    def test_pbx_readback_is_authenticated_get_only(self):
        result = {"authenticated": True, "operation": "GET /ari/channels", "channels": 0}
        with mock.patch.object(deploy.Native, "run", return_value=json.dumps(result).encode()) as run:
            self.assertEqual(deploy.Native().pbx_channels(), result)
        body = run.call_args.kwargs["body"].decode()
        self.assertIn("inject_asterisk_credentials", body); self.assertIn('method="GET"', body)
        self.assertNotIn("POST", body)


if __name__ == "__main__": unittest.main()
