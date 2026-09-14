#!/usr/bin/env python3
# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Fixture-only coverage for the one-guard transaction; no production access."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("ava_prior_message",
    Path(__file__).with_name("deploy-ava-prior-message-reference.py"))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class FakeNative:
    def __init__(self,root):
        self.root=root;self.running=True;self.container_id="fixture";self.mounts="m"*64
        self.events=[];self.counts={"active_calls":0,"active_sessions":0,"asterisk_channels":0}
        self.ari=True;self.channels=0;self.config_hash="config-a"
        self.logical={"sha256":"a"*64,"agent_count":2,"columns":["slug","extra_json"]}
        self.database=b"SQLite format 3 fixture evidence"
    def inspect(self):
        self.events.append("inspect")
        return {"id":self.container_id,"image":deploy.IMAGE,"running":self.running,"mounts_sha256":self.mounts}
    def syntax(self,name,body):self.events.append("syntax:"+name);compile(body,name,"exec")
    def installed_syntax(self):self.events.append("installed_syntax")
    def runtime_files(self,expected):
        self.events.append("runtime_files")
        actual={n:deploy.stable(deploy.fingerprint(self.root/n)) for n in deploy.TARGETS}
        if actual!={n:deploy.stable(expected[n]) for n in deploy.TARGETS}:
            raise deploy.Blocked("container_runtime_source_mismatch")
        return actual
    def stop(self):self.events.append("stop");self.running=False
    def start(self):self.events.append("start");self.running=True
    def health(self):
        self.events.append("health")
        return {"status":"healthy","ari_connected":self.ari,"config_hash":self.config_hash,**self.counts}
    def pbx_channels(self):
        self.events.append("pbx_channels")
        return {"authenticated":True,"operation":"GET /ari/channels","channels":self.channels}
    def configuration(self,include_capture=False):
        self.events.append("configuration_capture" if include_capture else "configuration")
        return deploy.protected_configuration(self.root.parent,include_capture)
    def agent_snapshot(self,include_backup=False):
        self.events.append("snapshot_backup" if include_backup else "snapshot")
        evidence={"sha256":deploy.sha256(self.database),"size":len(self.database)}
        return (copy.deepcopy(self.logical),self.database,evidence) if include_backup else copy.deepcopy(self.logical)

class TransactionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="ava-prior-message-fixture-")
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.root=self.base/"live/src";self.candidate=self.base/"candidate/src"
        self.backups=self.base/"backups"
        for root in (self.root,self.candidate):
            (root/"core").mkdir(parents=True,mode=0o700);root.chmod(0o700)
        for i,name in enumerate(deploy.TARGETS):
            (self.root/name).write_bytes(f"OLD_{i}=True\n".encode())
            (self.candidate/name).write_bytes(f"NEW_{i}=True\n".encode())
            mode=0o644
            (self.root/name).chmod(mode);(self.candidate/name).chmod(mode)
        (self.root.parent/"config").mkdir(mode=0o700)
        (self.root.parent/".env").write_bytes(b"PRIVATE_FIXTURE_VALUE=original\n")
        (self.root.parent/"config/ai-agent.yaml").write_bytes(b"main: preserved\nmini: preserved\n")
        (self.root.parent/"config/ai-agent.local.yaml").write_bytes(b"unknown: retained\n")
        (self.root.parent/"docker-compose.yml").write_bytes(b"services: {ai_engine: {}}\n")
        self.original=deploy.identities(self.root)
        self.after={n:deploy.stable(deploy.fingerprint(self.candidate/n)) for n in deploy.TARGETS}
        self.owner={"uid":os.geteuid(),"gid":os.getegid()}
        for name,value in [("BEFORE",{n:deploy.stable(v) for n,v in self.original.items()}),
                           ("AFTER",self.after),("STAGED",self.after),("STAGE_OWNER",self.owner)]:
            patch=mock.patch.object(deploy,name,value);patch.start();self.addCleanup(patch.stop)
        self.native=FakeNative(self.root)
    def apply(self,backup=None):
        return deploy.apply(self.native,self.root,self.candidate,self.backups,backup)
    def rollback(self,backup):
        return deploy.rollback(self.native,backup,self.root,self.backups)
    def assert_original(self):
        for name in deploy.TARGETS:
            actual=deploy.fingerprint(self.root/name)
            self.assertEqual(deploy.stable(actual),deploy.stable(self.original[name]))
            self.assertEqual(actual["mtime_ns"],self.original[name]["mtime_ns"])
    def test_check_only_nonactuating(self):
        result=deploy.check(self.native,self.root,self.candidate)
        self.assertEqual(result["logical_agent_config"],self.native.logical)
        self.assertNotIn("stop",self.native.events);self.assertFalse(self.backups.exists())
    def install_candidate(self):
        for name in deploy.TARGETS:
            body=(self.candidate/name).read_bytes()
            (self.root/name).write_bytes(body);(self.root/name).chmod(0o644)
    def test_installed_verify_accepts_after_and_is_nonactuating(self):
        self.install_candidate()
        result=deploy.verify_installed(self.native,self.root)
        self.assertEqual({n:deploy.stable(v) for n,v in result["installed"].items()},self.after)
        self.assertEqual(result["logical_agent_config"],self.native.logical)
        self.assertNotIn("stop",self.native.events);self.assertNotIn("start",self.native.events)
        self.assertFalse(self.backups.exists())
    def test_check_only_retains_before_state_semantics(self):
        self.install_candidate()
        with self.assertRaisesRegex(deploy.Blocked,"live_preimage_mismatch"):
            deploy.check(self.native,self.root,self.candidate)
        self.assertNotIn("stop",self.native.events);self.assertNotIn("start",self.native.events)
    def test_installed_verify_rejects_before_and_unknown(self):
        with self.assertRaisesRegex(deploy.Blocked,"installed_runtime_state_mismatch"):
            deploy.verify_installed(self.native,self.root)
        self.install_candidate();(self.root/deploy.TARGETS[0]).write_bytes(b"UNKNOWN=True\n")
        with self.assertRaisesRegex(deploy.Blocked,"installed_runtime_state_mismatch"):
            deploy.verify_installed(self.native,self.root)
    def test_installed_verify_rejects_mixed_state(self):
        second="core/fixture_second.py"
        (self.root/second).write_bytes(b"SECOND_OLD=True\n");(self.root/second).chmod(0o644)
        (self.candidate/second).write_bytes(b"SECOND_NEW=True\n");(self.candidate/second).chmod(0o644)
        targets=deploy.TARGETS+(second,)
        after={**self.after,second:deploy.stable(deploy.fingerprint(self.candidate/second))}
        with mock.patch.object(deploy,"TARGETS",targets),mock.patch.object(deploy,"AFTER",after):
            self.install_candidate()
            (self.root/second).write_bytes(b"SECOND_OLD=True\n")
            with self.assertRaisesRegex(deploy.Blocked,"installed_runtime_state_mismatch"):
                deploy.verify_installed(self.native,self.root)
    def test_installed_verify_rejects_health_ari_and_activity(self):
        for field,value,error in (("ari",False,"health_or_ari_not_ready"),
                                  ("active_calls",1,"quiescence_unavailable_or_active"),
                                  ("active_sessions",1,"quiescence_unavailable_or_active"),
                                  ("asterisk_channels",1,"quiescence_unavailable_or_active")):
            with self.subTest(field=field):
                self.native=FakeNative(self.root);self.install_candidate()
                if field=="ari":self.native.ari=value
                else:self.native.counts[field]=value
                with self.assertRaisesRegex(deploy.Blocked,error):
                    deploy.verify_installed(self.native,self.root)
    def test_installed_verify_rejects_native_pbx_activity(self):
        self.install_candidate();self.native.channels=1
        with self.assertRaisesRegex(deploy.Blocked,"native_pbx_zero_unavailable_or_active"):
            deploy.verify_installed(self.native,self.root)
    def test_installed_verify_rejects_config_and_logical_drift(self):
        for path in deploy.PROTECTED_PATHS:
            with self.subTest(path=path):
                self.native=FakeNative(self.root);self.install_candidate()
                original=self.native.configuration;calls=0
                def changed_config(*args,**kwargs):
                    nonlocal calls
                    calls+=1
                    value=original(*args,**kwargs)
                    if calls==2:
                        value=copy.deepcopy(value)
                        value[path]={**value[path],"sha256":"b"*64}
                    return value
                with mock.patch.object(self.native,"configuration",side_effect=changed_config):
                    with self.assertRaisesRegex(deploy.Blocked,"installed_protected_config_changed"):
                        deploy.verify_installed(self.native,self.root)
        self.native=FakeNative(self.root);calls=0
        def changed_logical(*args,**kwargs):
            nonlocal calls
            calls+=1;value=copy.deepcopy(self.native.logical)
            if calls==2:value["sha256"]="b"*64
            return value
        with mock.patch.object(self.native,"agent_snapshot",side_effect=changed_logical):
            with self.assertRaisesRegex(deploy.Blocked,"installed_logical_config_changed"):
                deploy.verify_installed(self.native,self.root)
    def test_installed_verify_rejects_config_hash_and_physical_drift(self):
        self.install_candidate();health_calls=0
        def changed_health():
            nonlocal health_calls
            health_calls+=1
            return {"status":"healthy","ari_connected":True,
                    "config_hash":"config-b" if health_calls==2 else "config-a",**self.native.counts}
        with mock.patch.object(self.native,"health",side_effect=changed_health):
            with self.assertRaisesRegex(deploy.Blocked,"installed_config_hash_changed"):
                deploy.verify_installed(self.native,self.root)
        self.native=FakeNative(self.root);inspect_calls=0
        def changed_inspect():
            nonlocal inspect_calls
            inspect_calls+=1
            return {"id":self.native.container_id,"image":deploy.IMAGE,"running":True,
                    "mounts_sha256":("n" if inspect_calls==2 else "m")*64}
        with mock.patch.object(self.native,"inspect",side_effect=changed_inspect):
            with self.assertRaisesRegex(deploy.Blocked,"container_identity_changed"):
                deploy.verify_installed(self.native,self.root)
    def test_one_guard_apply_rollback_and_evidence_separation(self):
        config=self.native.configuration();database=self.native.database
        backup,receipt=self.apply()
        self.assertEqual({n:deploy.stable(v) for n,v in deploy.identities(self.root).items()},self.after)
        self.assertEqual((backup/"evidence/agents-db-snapshot.sqlite").read_bytes(),database)
        self.assertTrue(receipt["agents_db_backup"]["evidence_only"])
        self.assertFalse(receipt["agents_db_backup"]["restored"])
        for name in deploy.TARGETS:
            self.assertEqual(deploy.fingerprint(backup/"runtime-before"/name)["mode"],"0600")
        self.rollback(backup);self.assert_original()
        self.assertEqual(self.native.configuration(),config);self.assertEqual(self.native.database,database)
    def test_replacement_failure_recovers_guard(self):
        original=deploy.replace_prepared
        for name in deploy.TARGETS:
            with self.subTest(target=name):
                def fail(temp,path,identity):
                    value=original(temp,path,identity)
                    if path==self.root/name and deploy.stable(identity)==self.after[name]:
                        raise OSError("dummy-after-rename")
                    return value
                with mock.patch.object(deploy,"replace_prepared",side_effect=fail):
                    with self.assertRaises(OSError):self.apply()
                self.assert_original();self.assertTrue(self.native.running)
                self.assertEqual(self.native.configuration(),deploy.protected_configuration(self.root.parent))
    def test_guard_prepared_and_journaled_before_rename(self):
        original=deploy.replace_prepared;seen=[]
        def observe(temp,path,identity):
            if path in [self.root/n for n in deploy.TARGETS] and deploy.stable(identity) in self.after.values():
                backup=next(self.backups.iterdir())
                receipt=json.loads((backup/"transaction.json").read_bytes())
                self.assertTrue(all(receipt["files"][n]["prepared"] is not None for n in deploy.TARGETS))
                self.assertEqual(deploy.stable(identity),self.after[path.relative_to(self.root).as_posix()])
                seen.append(path.relative_to(self.root).as_posix())
            return original(temp,path,identity)
        with mock.patch.object(deploy,"replace_prepared",side_effect=observe):self.apply()
        self.assertEqual(seen,list(deploy.TARGETS))
    def test_exception_after_first_rename_before_postimage_journal(self):
        original=deploy.replace_prepared
        def fail(temp,path,identity):
            value=original(temp,path,identity)
            if path==self.root/deploy.TARGETS[0] and deploy.stable(identity)==self.after[deploy.TARGETS[0]]:
                raise OSError("dummy")
            return value
        with mock.patch.object(deploy,"replace_prepared",side_effect=fail):
            with self.assertRaises(OSError):self.apply()
        self.assert_original()
    def test_directory_fsync_failure_after_target_replace_recovers(self):
        original_replace=deploy.replace_prepared;failed=[False]
        def fail_target_directory_fsync(temp,path,identity):
            if path!=self.root/deploy.TARGETS[0] or failed[0]:
                return original_replace(temp,path,identity)
            original_fsync=deploy.os.fsync
            def fsync(fd):
                if not failed[0] and __import__("stat").S_ISDIR(os.fstat(fd).st_mode):
                    failed[0]=True
                    raise OSError("fixture target directory fsync failure")
                return original_fsync(fd)
            with mock.patch.object(deploy.os,"fsync",side_effect=fsync):
                return original_replace(temp,path,identity)
        with mock.patch.object(deploy,"replace_prepared",side_effect=fail_target_directory_fsync):
            with self.assertRaises(OSError):self.apply()
        self.assertTrue(failed[0]);self.assert_original();self.assertTrue(self.native.running)
    def test_prepared_journal_recovers_after_process_boundary(self):
        backup,receipt=self.apply();path=backup/"transaction.json"
        row=receipt["files"][deploy.TARGETS[0]]
        row["postimage"]=None;receipt.update(status="captured",phase="replace:"+deploy.TARGETS[0])
        deploy.save_json(path,receipt)
        self.rollback(backup)
        self.assert_original();self.assertTrue(self.native.running)
    def test_receipt_failure_before_replace_recovers(self):
        original=deploy.save_json;failed=[False]
        def save(path,receipt):
            if receipt.get("phase")=="prepared" and not failed[0]:
                failed[0]=True;raise OSError("fixture prepared receipt failure")
            return original(path,receipt)
        with mock.patch.object(deploy,"save_json",side_effect=save):
            with self.assertRaises(OSError):self.apply()
        self.assertTrue(failed[0]);self.assert_original();self.assertTrue(self.native.running)
    def test_receipt_failure_after_replace_recovers(self):
        original=deploy.save_json;failed=[False]
        def save(path,receipt):
            row=receipt.get("files",{}).get(deploy.TARGETS[0],{})
            if receipt.get("phase","").startswith("replace:") and row.get("postimage") and not failed[0]:
                failed[0]=True;raise OSError("fixture postimage receipt failure")
            return original(path,receipt)
        with mock.patch.object(deploy,"save_json",side_effect=save):
            with self.assertRaises(OSError):self.apply()
        self.assertTrue(failed[0]);self.assert_original();self.assertTrue(self.native.running)
    def test_start_failure_recovers_old_pair_once(self):
        original=self.native.start;calls=[0]
        def fail():
            calls[0]+=1
            if calls[0]==1:raise deploy.Blocked("candidate_start_failure")
            original()
        self.native.start=fail
        with self.assertRaisesRegex(deploy.Blocked,"candidate_start_failure"):self.apply()
        self.assert_original();self.assertTrue(self.native.running);self.assertEqual(calls[0],2)
    def test_postflight_syntax_failure_recovers(self):
        original=self.native.installed_syntax;calls=[0]
        def fail():
            calls[0]+=1
            if calls[0]==1:raise deploy.Blocked("installed_syntax_failure")
            original()
        self.native.installed_syntax=fail
        with self.assertRaisesRegex(deploy.Blocked,"installed_syntax_failure"):self.apply()
        self.assert_original()
    def test_each_live_preimage_drift_blocks_before_stop(self):
        for name in deploy.TARGETS:
            original=(self.root/name).read_bytes();(self.root/name).write_bytes(b"FOREIGN=True\n")
            with self.assertRaisesRegex(deploy.Blocked,"live_preimage_mismatch"):self.apply()
            (self.root/name).write_bytes(original)
        self.assertNotIn("stop",self.native.events)
    def test_each_candidate_drift_blocks_before_stop(self):
        for name in deploy.TARGETS:
            original=(self.candidate/name).read_bytes();(self.candidate/name).write_bytes(b"FOREIGN=True\n")
            with self.assertRaisesRegex(deploy.Blocked,"candidate_mismatch"):self.apply()
            (self.candidate/name).write_bytes(original)
        self.assertNotIn("stop",self.native.events)
    def test_runtime_changes_during_native_check(self):
        original=self.native.syntax
        def changed(name,body):
            original(name,body)
            if name==deploy.TARGETS[0]:os.utime(self.root/deploy.TARGETS[0],ns=(1,1))
        self.native.syntax=changed
        with self.assertRaisesRegex(deploy.Blocked,"live_state_changed_during_check"):self.apply()
        self.assertNotIn("stop",self.native.events)
    def test_candidate_file_owner_checked_separately(self):
        original=deploy.fingerprint
        def wrong(path):
            result=original(path)
            if path==self.candidate/deploy.TARGETS[0]:result["uid"]=9999
            return result
        with mock.patch.object(deploy,"fingerprint",side_effect=wrong):
            with self.assertRaisesRegex(deploy.Blocked,"candidate_mismatch"):self.apply()
        self.assertNotIn("stop",self.native.events)
    def test_candidate_stage_owner_checked(self):
        with mock.patch.object(deploy,"STAGE_OWNER",{"uid":9999,"gid":9999}):
            with self.assertRaisesRegex(deploy.Blocked,"candidate_stage_owner_or_mode"):self.apply()
    def test_candidate_stage_mode_checked(self):
        self.candidate.chmod(0o755)
        with self.assertRaisesRegex(deploy.Blocked,"candidate_stage_owner_or_mode"):self.apply()
    def test_health_calls_sessions_channels_each_block(self):
        for key in self.native.counts:
            self.native.counts[key]=1
            with self.assertRaisesRegex(deploy.Blocked,"quiescence"):self.apply()
            self.native.counts[key]=0
        self.assertNotIn("stop",self.native.events)
    def test_health_boolean_zero_is_not_native_count(self):
        self.native.counts["active_calls"]=False
        with self.assertRaisesRegex(deploy.Blocked,"quiescence"):self.apply()
    def test_health_ari_disconnected_blocks(self):
        self.native.ari=False
        with self.assertRaisesRegex(deploy.Blocked,"health_or_ari"):self.apply()
    def test_health_missing_counter_blocks(self):
        del self.native.counts["active_sessions"]
        with self.assertRaisesRegex(deploy.Blocked,"quiescence"):self.apply()
    def test_native_ari_busy_blocks_without_stop(self):
        self.native.channels=1
        with self.assertRaisesRegex(deploy.Blocked,"native_pbx_zero"):self.apply()
        self.assertNotIn("stop",self.native.events)
    def test_native_ari_boolean_zero_is_rejected(self):
        self.native.channels=False
        with self.assertRaisesRegex(deploy.Blocked,"native_pbx_zero"):self.apply()
    def test_preexisting_backup_refused(self):
        self.backups.mkdir();backup=self.backups/"existing";backup.mkdir();(backup/"marker").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):self.apply(backup)
        self.assertEqual((backup/"marker").read_bytes(),b"preserve");self.assertNotIn("stop",self.native.events)
    def test_backup_outside_base_refused(self):
        with self.assertRaisesRegex(deploy.Blocked,"backup_outside_base"):self.apply(self.base/"outside")
    def test_config_drift_before_stop_no_rewind(self):
        original=self.native.agent_snapshot;calls=[0];path=self.root.parent/"config/ai-agent.local.yaml"
        def changed(include_backup=False):
            calls[0]+=1
            result=original(include_backup)
            if calls[0]==3:path.write_bytes(b"foreign: preserved\n")
            return result
        self.native.agent_snapshot=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
        self.assertNotIn("stop",self.native.events);self.assert_original()
        self.assertEqual(path.read_bytes(),b"foreign: preserved\n")
    def test_postflight_config_drift_blocks_recovery_without_another_stop_or_restart(self):
        original=self.native.start;path=self.root.parent/"config/ai-agent.local.yaml";calls=[0]
        def changed():
            original();calls[0]+=1
            if calls[0]==1:path.write_bytes(b"foreign: preserved\n")
        self.native.start=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
        self.assertEqual({n:deploy.stable(v) for n,v in deploy.identities(self.root).items()},self.after)
        self.assertEqual(path.read_bytes(),b"foreign: preserved\n")
        self.assertEqual(self.native.events.count("stop"),1);self.assertEqual(self.native.events.count("start"),1)
        receipt=json.loads((next(self.backups.iterdir())/"transaction.json").read_bytes())
        self.assertFalse(receipt.get("rollback_runtime_files_restored",False))
        self.assertEqual(receipt["rollback_validation"],"blocked")
        self.assertEqual(receipt["rollback_failure"],"protected_config_changed_before_restore")
    def test_logical_agent_drift_not_rewound(self):
        original=self.native.start
        def changed():original();self.native.logical["sha256"]="b"*64
        self.native.start=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
        self.assertEqual({n:deploy.stable(v) for n,v in deploy.identities(self.root).items()},self.after)
        self.assertEqual(self.native.logical["sha256"],"b"*64)
        self.assertEqual(self.native.events.count("stop"),1);self.assertEqual(self.native.events.count("start"),1)
    def test_config_hash_drift_reported(self):
        original=self.native.start
        def changed():original();self.native.config_hash="changed"
        self.native.start=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
        self.assertEqual({n:deploy.stable(v) for n,v in deploy.identities(self.root).items()},self.after)
        self.assertEqual(self.native.events.count("stop"),1);self.assertEqual(self.native.events.count("start"),1)
    def test_manual_rollback_config_drift_blocks_before_stop_or_replace(self):
        backup,_=self.apply();path=self.root.parent/"config/ai-agent.local.yaml"
        path.write_bytes(b"foreign: preserved\n")
        identities=deploy.identities(self.root);events=self.native.events.copy()
        with self.assertRaisesRegex(deploy.Blocked,"protected_config_changed_before_restore"):self.rollback(backup)
        self.assertEqual(deploy.identities(self.root),identities)
        self.assertEqual(self.native.events.count("stop"),events.count("stop"))
        self.assertEqual(self.native.events.count("start"),events.count("start"))
    def test_manual_rollback_logical_drift_blocks_before_stop_or_replace(self):
        backup,_=self.apply();self.native.logical["sha256"]="b"*64
        identities=deploy.identities(self.root);events=self.native.events.copy()
        with self.assertRaisesRegex(deploy.Blocked,"logical_config_changed_before_restore"):self.rollback(backup)
        self.assertEqual(deploy.identities(self.root),identities)
        self.assertEqual(self.native.events.count("stop"),events.count("stop"))
        self.assertEqual(self.native.events.count("start"),events.count("start"))
    def test_manual_rollback_ari_false_blocks_before_stop_or_replace(self):
        backup,_=self.apply();self.native.ari=False
        identities=deploy.identities(self.root);events=self.native.events.copy()
        with self.assertRaisesRegex(deploy.Blocked,"health_or_ari_not_ready"):self.rollback(backup)
        self.assertEqual(deploy.identities(self.root),identities)
        self.assertEqual(self.native.events.count("stop"),events.count("stop"))
        self.assertEqual(self.native.events.count("start"),events.count("start"))
    def test_rollback_config_drift_during_readback_blocks_before_stop(self):
        backup,_=self.apply();original=self.native.agent_snapshot
        path=self.root.parent/"config/ai-agent.local.yaml"
        def changed(include_backup=False):
            result=original(include_backup);path.write_bytes(b"foreign: preserved\n");return result
        self.native.agent_snapshot=changed;identities=deploy.identities(self.root);events=self.native.events.copy()
        with self.assertRaisesRegex(deploy.Blocked,"protected_config_changed_before_restore"):self.rollback(backup)
        self.assertEqual(deploy.identities(self.root),identities)
        self.assertEqual(self.native.events.count("stop"),events.count("stop"))
        self.assertEqual(self.native.events.count("start"),events.count("start"))
    def test_foreign_postimage_blocks_manual_rollback(self):
        backup,_=self.apply();(self.root/deploy.TARGETS[0]).write_bytes(b"FOREIGN=True\n")
        stops=self.native.events.count("stop")
        with self.assertRaisesRegex(deploy.Blocked,"runtime_file_conflict"):self.rollback(backup)
        self.assertEqual(self.native.events.count("stop"),stops)
        self.assertEqual((self.root/deploy.TARGETS[0]).read_bytes(),b"FOREIGN=True\n")
    def test_drift_while_stopped_never_overwritten(self):
        original=self.native.stop
        def changed():original();(self.root/deploy.TARGETS[0]).write_bytes(b"FOREIGN=True\n")
        self.native.stop=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
        self.assertEqual((self.root/deploy.TARGETS[0]).read_bytes(),b"FOREIGN=True\n")
    def test_container_identity_drift_blocks(self):
        original=self.native.stop
        def changed():original();self.native.container_id="foreign"
        self.native.stop=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
    def test_mount_inventory_drift_blocks(self):
        original=self.native.stop
        def changed():original();self.native.mounts="foreign"
        self.native.stop=changed
        with self.assertRaisesRegex(deploy.Blocked,"automatic_rollback_blocked"):self.apply()
    def test_rollback_idempotent_no_extra_replace_or_restart(self):
        backup,_=self.apply();self.rollback(backup)
        identities=deploy.identities(self.root);stops=self.native.events.count("stop");starts=self.native.events.count("start")
        receipt_identity=deploy.fingerprint(backup/"transaction.json")
        self.rollback(backup)
        self.assertEqual(deploy.identities(self.root),identities)
        self.assertEqual(deploy.fingerprint(backup/"transaction.json"),receipt_identity)
        self.assertEqual(self.native.events.count("stop"),stops);self.assertEqual(self.native.events.count("start"),starts)
    def test_wrong_restored_bytes_are_detected(self):
        backup,_=self.apply();original=deploy.replace_prepared;changed=[False]
        def corrupt_after_restore(temp,path,identity):
            result=original(temp,path,identity)
            if path==self.root/deploy.TARGETS[0] and deploy.stable(identity)==deploy.stable(self.original[deploy.TARGETS[0]]) and not changed[0]:
                changed[0]=True;path.write_bytes(b"WRONG_RESTORED_BYTES=True\n")
            return result
        with mock.patch.object(deploy,"replace_prepared",side_effect=corrupt_after_restore):
            with self.assertRaisesRegex(deploy.Blocked,"postflight_runtime_files_mismatch|container_runtime_source_mismatch"):self.rollback(backup)
        self.assertTrue(changed[0])
    def test_each_physical_config_drift_blocks_rollback_without_rewind(self):
        backup,_=self.apply()
        for name in deploy.PROTECTED_PATHS:
            with self.subTest(name=name):
                path=self.root.parent/name
                original=path.read_bytes();original_identity=deploy.fingerprint(path)
                identities=deploy.identities(self.root);events=self.native.events.copy()
                path.write_bytes(original+b"# foreign\n")
                with self.assertRaisesRegex(deploy.Blocked,"protected_config_changed_before_restore"):
                    self.rollback(backup)
                self.assertEqual(deploy.identities(self.root),identities)
                self.assertEqual(self.native.events.count("stop"),events.count("stop"))
                self.assertEqual(self.native.events.count("start"),events.count("start"))
                path.write_bytes(original)
                os.chmod(path,int(original_identity["mode"],8))
                ns=int(original_identity["mtime_ns"]);os.utime(path,ns=(ns,ns))
        self.rollback(backup);self.assert_original()
    def test_tampered_runtime_backup_blocks(self):
        backup,_=self.apply();(backup/"runtime-before"/deploy.TARGETS[0]).write_bytes(b"foreign")
        with self.assertRaisesRegex(deploy.Blocked,"runtime_backup_mismatch"):self.rollback(backup)
    def test_database_evidence_cannot_authorize_rewind(self):
        backup,_=self.apply();path=backup/"transaction.json";receipt=json.loads(path.read_bytes())
        receipt["agents_db_backup"]["restored"]=True;deploy.save_json(path,receipt)
        with self.assertRaisesRegex(deploy.Blocked,"database_evidence_contract"):self.rollback(backup)
    def test_tampered_database_capture_blocks(self):
        backup,_=self.apply();(backup/"evidence/agents-db-snapshot.sqlite").write_bytes(b"foreign")
        with self.assertRaisesRegex(deploy.Blocked,"database_evidence_contract"):self.rollback(backup)
    def test_tampered_protected_config_capture_blocks(self):
        backup,_=self.apply();(backup/"evidence/protected-config/.env").write_bytes(b"foreign")
        with self.assertRaisesRegex(deploy.Blocked,"protected_config_evidence"):self.rollback(backup)
    def test_receipt_source_commit_mismatch_blocks(self):
        backup,_=self.apply();path=backup/"transaction.json";receipt=json.loads(path.read_bytes())
        receipt["commit"]="foreign";deploy.save_json(path,receipt)
        with self.assertRaisesRegex(deploy.Blocked,"receipt_identity"):self.rollback(backup)
    def test_receipt_cannot_expand_target_set(self):
        backup,_=self.apply();path=backup/"transaction.json";receipt=json.loads(path.read_bytes())
        receipt["files"]["other.py"]={};deploy.save_json(path,receipt)
        with self.assertRaisesRegex(deploy.Blocked,"receipt_identity"):self.rollback(backup)
    def test_backup_mode_not_protected_blocks(self):
        backup,_=self.apply();backup.chmod(0o755)
        with self.assertRaisesRegex(deploy.Blocked,"backup_path_owner_or_mode"):self.rollback(backup)
    def test_symlink_runtime_rejected(self):
        name=deploy.TARGETS[0];real=self.root/name;moved=self.base/"moved.py";real.rename(moved);real.symlink_to(moved)
        with self.assertRaisesRegex(deploy.Blocked,"symlink_path"):self.apply()
    def test_hardlinked_runtime_rejected(self):
        os.link(self.root/deploy.TARGETS[0],self.base/"hard.py")
        with self.assertRaisesRegex(deploy.Blocked,"not_single_regular_file"):self.apply()
    def test_lock_exclusive_and_preexisting_mode_not_rewritten(self):
        self.backups.mkdir();path=self.backups/deploy.LOCK_NAME;path.write_bytes(b"preserve");path.chmod(0o644)
        with self.assertRaisesRegex(deploy.Blocked,"lock_owner_mode_or_type"):
            deploy.with_lock(lambda:None,self.backups)
        self.assertEqual(path.read_bytes(),b"preserve");path.chmod(0o600)
        def second():
            with self.assertRaises(BlockingIOError):deploy.with_lock(lambda:None,self.backups)
        deploy.with_lock(second,self.backups)
    def test_check_only_cli_never_uses_lock(self):
        checked={"health":self.native.health(),"logical_agent_config":self.native.logical,
                 "configuration":self.native.configuration()}
        with mock.patch.object(deploy,"COMMIT","a"*40),mock.patch.object(deploy,"SOURCE_TREE","b"*40),mock.patch.object(deploy,"check",return_value=checked),mock.patch.object(deploy,"with_lock") as lock:
            with mock.patch("builtins.print"):self.assertEqual(deploy.main(["--check-only"]),0)
        lock.assert_not_called()
    def test_installed_verify_cli_never_uses_lock(self):
        verified={"health":self.native.health(),"logical_agent_config":self.native.logical,
                  "configuration":self.native.configuration(),"installed":self.after}
        with mock.patch.object(deploy,"COMMIT","a"*40),mock.patch.object(deploy,"SOURCE_TREE","b"*40),mock.patch.object(deploy,"verify_installed",return_value=verified),mock.patch.object(deploy,"with_lock") as lock:
            with mock.patch("builtins.print") as output:
                self.assertEqual(deploy.main(["--verify-installed"]),0)
        lock.assert_not_called()
        envelope=json.loads(output.call_args.args[0])
        self.assertEqual(envelope["status"],"installed_verify_pass")
    def test_unbound_cli_cannot_inspect_or_mutate(self):
        with mock.patch.object(deploy,"COMMIT",None),mock.patch.object(deploy,"Native") as native:
            with mock.patch("builtins.print"):self.assertEqual(deploy.main(["--check-only"]),1)
        native.assert_not_called()

class NativeContractTests(unittest.TestCase):
    def test_native_inspect_matches_actual_source_bind_and_image(self):
        response={"id":"fixture","image":deploy.IMAGE,"running":True,"mounts":[
            {"Destination":"/app/src","Source":str(deploy.LIVE_ROOT),"Type":"bind","RW":True}]}
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()) as run:
            self.assertEqual(deploy.Native().inspect()["image"],deploy.IMAGE)
            response["image"]="foreign"
            run.return_value=json.dumps(response).encode()
            with self.assertRaisesRegex(deploy.Blocked,"source_mount_or_image"):deploy.Native().inspect()
    def test_native_inspect_foreign_bind_rejected(self):
        response={"id":"fixture","image":deploy.IMAGE,"running":True,"mounts":[
            {"Destination":"/app/src","Source":"/foreign","Type":"bind","RW":True}]}
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()):
            with self.assertRaisesRegex(deploy.Blocked,"source_mount_or_image"):deploy.Native().inspect()
    def test_native_shuffled_multi_mount_array_preserves_identity(self):
        mounts=[
            {"Destination":"/app/src","Source":str(deploy.LIVE_ROOT),"Type":"bind","RW":True,"Propagation":"rprivate"},
            {"Destination":"/app/data","Source":"/fixture/data","Type":"bind","RW":True,"Propagation":"rprivate"},
            {"Destination":"/app/config","Source":"/fixture/config","Type":"bind","RW":False,"Propagation":"rprivate"}]
        response={"id":"fixture","image":deploy.IMAGE,"running":True,"mounts":mounts}
        with mock.patch.object(deploy.Native,"run") as run:
            run.return_value=json.dumps(response).encode();anchor=deploy.Native().inspect()
            for order in (list(reversed(mounts)),[mounts[1],mounts[0],mounts[2]]):
                response["mounts"]=order;run.return_value=json.dumps(response).encode()
                self.assertEqual(deploy.require_container(deploy.Native(),anchor,running=True),anchor)
        expected=sorted(mounts,key=lambda m:(m["Destination"],m["Source"],m["Type"]))
        self.assertEqual(anchor["mounts_sha256"],deploy.sha256(json.dumps(
            expected,sort_keys=True,separators=(",",":")).encode()))
    def test_native_mount_identity_retains_every_record_field(self):
        mounts=[
            {"Destination":"/app/src","Source":str(deploy.LIVE_ROOT),"Type":"bind","RW":True,"Propagation":"rprivate"},
            {"Destination":"/app/data","Source":"/fixture/data","Type":"volume","RW":True,
             "Name":"fixture","Driver":"local","Mode":"z","Propagation":"","UnknownNested":{"kept":True}}]
        response={"id":"fixture","image":deploy.IMAGE,"running":True,"mounts":mounts}
        with mock.patch.object(deploy.Native,"run") as run:
            run.return_value=json.dumps(response).encode();anchor=deploy.Native().inspect()
            for key in mounts[1]:
                changed=copy.deepcopy(response)
                changed["mounts"][1][key]={"different":True} if key=="UnknownNested" else "changed"
                run.return_value=json.dumps(changed).encode()
                with self.assertRaisesRegex(deploy.Blocked,"container_identity_changed"):
                    deploy.require_container(deploy.Native(),anchor,running=True)
    def test_native_mount_addition_removal_and_duplicates_change_identity(self):
        source={"Destination":"/app/src","Source":str(deploy.LIVE_ROOT),"Type":"bind","RW":True}
        data={"Destination":"/app/data","Source":"/fixture/data","Type":"bind","RW":True}
        response={"id":"fixture","image":deploy.IMAGE,"running":True,"mounts":[source,data]}
        with mock.patch.object(deploy.Native,"run") as run:
            run.return_value=json.dumps(response).encode();anchor=deploy.Native().inspect()
            for changed in ([source],[source,data,copy.deepcopy(data)],
                            [source,data,{**data,"Destination":"/app/cache"}]):
                response["mounts"]=changed;run.return_value=json.dumps(response).encode()
                with self.assertRaisesRegex(deploy.Blocked,"container_identity_changed"):
                    deploy.require_container(deploy.Native(),anchor,running=True)
    def test_native_snapshot_readonly_and_backup_api(self):
        response={"sha256":"a"*64,"agent_count":2,"columns":["slug"]}
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()) as run:
            deploy.Native().agent_snapshot()
        body=run.call_args.kwargs["body"].decode()
        self.assertIn("mode=ro",body);self.assertIn("c.backup(d)",body)
        self.assertIn("EngineAgentStore().db_path",body);self.assertNotIn("immutable=1",body)
        self.assertNotIn("delete from",body.lower());self.assertNotIn("update agents",body.lower())
    def test_native_snapshot_backup_decodes_privately(self):
        database=b"SQLite fixture"
        response={"sha256":"a"*64,"agent_count":2,"columns":["slug"],
          "backup_b64":__import__("base64").b64encode(database).decode(),
          "backup_sha256":deploy.sha256(database),"backup_size":len(database)}
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()):
            logical,body,evidence=deploy.Native().agent_snapshot(True)
        self.assertEqual(body,database);self.assertNotIn("backup_b64",logical)
        self.assertEqual(evidence["sha256"],deploy.sha256(database))
    def test_native_runtime_binding_checks_only_guard(self):
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(deploy.BEFORE).encode()) as run:
            deploy.Native().runtime_files(deploy.BEFORE)
        body=run.call_args.kwargs["body"].decode()
        self.assertEqual(deploy.TARGETS,("core/pipeline_message_deposit.py",))
        for name in deploy.TARGETS:self.assertIn(name,body)
        self.assertIn("/app/src",body)
        self.assertNotIn("print(b)",body)
    def test_native_guard_binding_missing_or_changed_blocks(self):
        for name in deploy.TARGETS:
            for missing in (False,True):
                with self.subTest(target=name,missing=missing):
                    response=copy.deepcopy(deploy.BEFORE)
                    if missing:response.pop(name)
                    else:response[name]["sha256"]="0"*64
                    with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()):
                        with self.assertRaises(deploy.Blocked):deploy.Native().runtime_files(deploy.BEFORE)
    def test_native_compile_installed_guard_path(self):
        with mock.patch.object(deploy.Native,"run",return_value=b"") as run:
            deploy.Native().installed_syntax()
        body=run.call_args.kwargs["body"].decode()
        for name in deploy.TARGETS:self.assertIn("/app/src/"+name,body)
        self.assertIn("compile(",body)
    def test_native_candidate_compile_uses_target_name(self):
        with mock.patch.object(deploy.Native,"run",return_value=b"") as run:
            deploy.Native().syntax("core/pipeline_message_deposit.py",b"OK=True\n")
        self.assertIn("core/pipeline_message_deposit.py",run.call_args.args[0][-1])
    def test_mutating_container_commands_have_no_outer_timeout_or_retry(self):
        with mock.patch.object(deploy.Native,"run",return_value=b"") as run:
            native=deploy.Native();native.stop();native.start()
        self.assertEqual(run.call_count,2)
        self.assertEqual(run.call_args_list[0].args[0],
            ["docker","stop","--time","30","ai_engine"])
        self.assertEqual(run.call_args_list[0].kwargs["timeout"],None)
        self.assertEqual(run.call_args_list[1].args[0],["docker","start","ai_engine"])
        self.assertEqual(run.call_args_list[1].kwargs["timeout"],None)
    def test_native_ari_authenticated_get_and_explicit_shape(self):
        response={"authenticated":True,"operation":"GET /ari/channels","channels":0}
        with mock.patch.object(deploy.Native,"run",return_value=json.dumps(response).encode()) as run:
            self.assertEqual(deploy.Native().pbx_channels(),response)
        body=run.call_args.kwargs["body"].decode()
        self.assertIn("inject_asterisk_credentials",body);self.assertIn('method="GET"',body)
        self.assertNotIn("assert ",body);self.assertNotIn("POST",body)
    def test_native_ari_malformed_blocks(self):
        with mock.patch.object(deploy.Native,"run",return_value=b"null"):
            with self.assertRaisesRegex(deploy.Blocked,"native_pbx_zero"):deploy.Native().pbx_channels()
    def test_no_operational_assert_or_broad_actuation(self):
        text=Path(deploy.__file__).read_text()
        self.assertNotIn("assert ",text)
        for forbidden in ("systemctl","/reload","docker compose","git checkout","run_call"):
            self.assertNotIn(forbidden,text)

if __name__=="__main__":
    unittest.main()
