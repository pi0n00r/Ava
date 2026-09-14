#!/usr/bin/env python3
# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Exact-CAS Ava engine.py activation for the HTTP-tool advertisement release."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import urllib.request

COMMIT = "fbb1e2d7a03234dc9fbfb85ba5f79a1bb5a0e9f5"
SOURCE_TREE = "f0e944af4fc48cf366695071e71897401356dbc6"
IMAGE = "sha256:91ae246a07be78ff38ac4d5e95bcaa1deedac17bbaa1ae96c0e379455d04f2c7"
LIVE_ROOT = Path("/opt/AVA-AI-Voice-Agent-for-Asterisk/src")
CANDIDATE_ROOT = Path(__file__).resolve().parent.parent / "src"
BACKUP_BASE = Path("/home/aimee/.local/share/ava-rollback")
TARGET = "engine.py"
OWNER = {"uid": 1001, "gid": 1001}
BEFORE = {"sha256": "4d9da9e3b076aa399152f11a4c68c8e11922d87f6e892b27d0717b63f7351471",
          "size": 1115279, "mode": "0644", **OWNER}
AFTER = {"sha256": "b0af355fab439b3d6eb08eabf33df5adc1a8d77d4dfd5b5921b0d9085cb7ac4a",
         "size": 1116285, "mode": "0644", **OWNER}


class Blocked(Exception):
    pass


def safe_path(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise Blocked("symlink_path")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(path):
    safe_path(path)
    try:
        before = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise Blocked("not_single_regular_file")
    data = path.read_bytes()
    after = path.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode", "st_uid", "st_gid")
    if tuple(getattr(before, k) for k in fields) != tuple(getattr(after, k) for k in fields):
        raise Blocked("concurrent_read_change")
    return {"sha256": sha256(data), "size": after.st_size,
            "mode": format(stat.S_IMODE(after.st_mode), "04o"),
            "uid": after.st_uid, "gid": after.st_gid,
            "mtime_ns": str(after.st_mtime_ns), "dev": after.st_dev, "ino": after.st_ino}


def stable(identity):
    return {k: identity[k] for k in ("sha256", "size", "mode", "uid", "gid")}


def atomic_bytes(path, data, metadata, before_replace=None):
    safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".ava-http-advert-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchown(stream.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(stream.fileno(), int(metadata["mode"], 8))
            if metadata.get("mtime_ns") is not None:
                value = int(metadata["mtime_ns"])
                os.utime(stream.fileno(), ns=(value, value))
            os.fsync(stream.fileno())
        if before_replace:
            before_replace()
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return fingerprint(path)


def save_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(),
                 {"uid": os.geteuid(), "gid": os.getegid(), "mode": "0600"})


class Native:
    @staticmethod
    def run(args, stage, *, body=None, timeout=45):
        try:
            result = subprocess.run(args, input=body, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise Blocked(stage + "_timeout") from None
        if result.returncode:
            raise Blocked(stage + "_exit_" + str(result.returncode))
        return result.stdout

    def inspect(self):
        raw = self.run(["docker", "inspect", "--format",
                        '{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},"mounts":{{json .Mounts}}}',
                        "ai_engine"], "container_inspect")
        try:
            value = json.loads(raw)
            mounts = [m for m in value["mounts"] if m["Destination"] == "/app/src"]
            if (value["image"] != IMAGE or len(mounts) != 1 or mounts[0]["Type"] != "bind"
                    or mounts[0]["Source"] != str(LIVE_ROOT) or mounts[0]["RW"] is not True):
                raise Blocked("source_mount_or_image_mismatch")
            return {k: value[k] for k in ("id", "image", "running")}
        except (KeyError, TypeError, ValueError):
            raise Blocked("container_inspect_malformed") from None

    def syntax(self, source):
        self.run(["docker", "exec", "-i", "-e", "PYTHONDONTWRITEBYTECODE=1", "ai_engine",
                  "python", "-B", "-c", "import sys;compile(sys.stdin.buffer.read(),'engine.py','exec')"],
                 "candidate_container_syntax", body=source)

    def installed_syntax(self):
        self.run(["docker", "exec", "-e", "PYTHONDONTWRITEBYTECODE=1", "ai_engine",
                  "python", "-B", "-c",
                  "from pathlib import Path;compile(Path('/app/src/engine.py').read_bytes(),'engine.py','exec')"],
                 "installed_container_syntax")

    def stop(self):
        self.run(["docker", "stop", "--time", "30", "ai_engine"], "container_stop")

    def start(self):
        self.run(["docker", "start", "ai_engine"], "container_start")

    def agent_snapshot(self, include_backup=False):
        code = r'''import base64,hashlib,json,sqlite3,tempfile,os
p="/app/data/operator/agents.db"
c=sqlite3.connect("file:"+p+"?mode=ro",uri=True)
cols=[r[1] for r in c.execute("pragma table_info(agents)")]
keep=[v for v in cols if v != "updated_at"]
rows=[dict(zip(keep,r)) for r in c.execute("select "+",".join('"'+v.replace('"','""')+'"' for v in keep)+" from agents order by slug")]
payload=json.dumps({"columns":keep,"rows":rows},sort_keys=True,separators=(",",":"),default=str).encode()
out={"sha256":hashlib.sha256(payload).hexdigest(),"agent_count":len(rows),"columns":keep}
if ''' + ("True" if include_backup else "False") + r''':
 fd,q=tempfile.mkstemp(prefix="ava-agents-",suffix=".sqlite");os.close(fd)
 try:
  d=sqlite3.connect(q);c.backup(d);d.close();b=open(q,"rb").read()
  out["backup_b64"]=base64.b64encode(b).decode();out["backup_sha256"]=hashlib.sha256(b).hexdigest();out["backup_size"]=len(b)
 finally: os.unlink(q)
print(json.dumps(out,sort_keys=True))'''
        raw = self.run(["docker", "exec", "-i", "ai_engine", "python", "-B", "-"],
                       "logical_agent_snapshot", body=code.encode(), timeout=30)
        try:
            value = json.loads(raw)
            if (not isinstance(value.get("sha256"), str) or len(value["sha256"]) != 64
                    or type(value.get("agent_count")) is not int or not isinstance(value.get("columns"), list)):
                raise ValueError
            if include_backup:
                data = base64.b64decode(value.pop("backup_b64"), validate=True)
                if sha256(data) != value["backup_sha256"] or len(data) != value["backup_size"]:
                    raise ValueError
                return value, data
            return value
        except (KeyError, TypeError, ValueError):
            raise Blocked("logical_agent_snapshot_malformed") from None

    def health(self):
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen("http://localhost:15000/health", timeout=2) as response:
                    value = json.loads(response.read(65537))
                if (response.status == 200 and value.get("status") == "healthy"
                        and all(type(value.get(k)) is int for k in ("active_calls", "active_sessions", "asterisk_channels"))):
                    return {k: value.get(k) for k in ("status", "active_calls", "active_sessions",
                                                       "asterisk_channels", "config_hash")}
            except (OSError, TypeError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise Blocked("health_readback_failed")
            time.sleep(0.5)

    def pbx_channels(self):
        code = r'''import base64,json,ssl,urllib.request
from src.config.security import inject_asterisk_credentials
d={};inject_asterisk_credentials(d);a=d["asterisk"]
url=f'{a["scheme"]}://{a["host"]}:{a["port"]}/ari/channels'
h=base64.b64encode((a["username"]+":"+a["password"]).encode()).decode()
ctx=None if a["ssl_verify"] else ssl._create_unverified_context()
with urllib.request.urlopen(urllib.request.Request(url,headers={"Authorization":"Basic "+h},method="GET"),timeout=5,context=ctx) as r:
 rows=json.loads(r.read(1048577));assert r.status==200 and isinstance(rows,list)
print(json.dumps({"authenticated":True,"operation":"GET /ari/channels","channels":len(rows)}))'''
        raw = self.run(["docker", "exec", "-i", "ai_engine", "python", "-B", "-"],
                       "native_pbx_readback", body=code.encode(), timeout=10)
        try:
            value = json.loads(raw)
            if (value != {"authenticated": True, "operation": "GET /ari/channels", "channels": 0}):
                raise ValueError
            return value
        except (TypeError, ValueError):
            raise Blocked("native_pbx_zero_unavailable_or_active") from None


def require_zero(health):
    if any(health.get(k) != 0 or type(health.get(k)) is not int
           for k in ("active_calls", "active_sessions", "asterisk_channels")):
        raise Blocked("quiescence_unavailable_or_active")


def require_container(native, anchor=None, running=None):
    value = native.inspect()
    if anchor and any(value[k] != anchor[k] for k in ("id", "image")):
        raise Blocked("container_identity_changed")
    if running is not None and value["running"] is not running:
        raise Blocked("container_running_state_mismatch")
    return value


def check(native, root=LIVE_ROOT, candidate=CANDIDATE_ROOT):
    before = fingerprint(root / TARGET)
    if before is None or stable(before) != BEFORE:
        raise Blocked("live_engine_preimage_mismatch")
    candidate_id = fingerprint(candidate / TARGET)
    if candidate_id is None or stable(candidate_id) != AFTER:
        raise Blocked("candidate_engine_mismatch")
    source = (candidate / TARGET).read_bytes()
    compile(source, TARGET, "exec")
    anchor = require_container(native, running=True)
    native.syntax(source)
    health = native.health(); require_zero(health)
    native.pbx_channels()
    logical = native.agent_snapshot()
    if fingerprint(root / TARGET) != before:
        raise Blocked("live_engine_changed_during_check")
    require_container(native, anchor, running=True)
    return {"before": before, "anchor": anchor, "health": health,
            "logical_agent_config": logical, "source": source}


def restore(native, backup, receipt, root=LIVE_ROOT):
    current = fingerprint(root / TARGET)
    if current not in (receipt["before"], receipt.get("postimage")):
        raise Blocked("rollback_engine_conflict")
    anchor = receipt["container"]
    require_container(native, anchor)
    if current == receipt["before"] and native.inspect()["running"]:
        return
    if native.inspect()["running"]:
        health = native.health(); require_zero(health); native.stop()
    require_container(native, anchor, running=False)
    if fingerprint(root / TARGET) == receipt.get("postimage"):
        original = backup / TARGET
        original_id = fingerprint(original)
        if (stable(original_id) != stable(receipt["before"])
                or original_id["mtime_ns"] != receipt["before"]["mtime_ns"]):
            raise Blocked("backup_engine_mismatch")
        atomic_bytes(root / TARGET, original.read_bytes(), receipt["before"])
    native.start(); require_container(native, anchor, running=True)
    health = native.health(); require_zero(health)
    native.pbx_channels()
    logical = native.agent_snapshot()
    if logical != receipt["logical_agent_config"] or health.get("config_hash") != receipt["preflight_health"].get("config_hash"):
        raise Blocked("rollback_logical_config_changed")


def apply(native, root=LIVE_ROOT, candidate=CANDIDATE_ROOT, base=BACKUP_BASE):
    checked = check(native, root, candidate)
    safe_path(base); base.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="http-advert-fbb1e2d-", dir=base)); os.chmod(backup, 0o700)
    snapshot, database = native.agent_snapshot(include_backup=True)
    if snapshot != checked["logical_agent_config"]:
        raise Blocked("logical_agent_config_changed_before_capture")
    atomic_bytes(backup / TARGET, (root / TARGET).read_bytes(), checked["before"])
    atomic_bytes(backup / "agents-db-snapshot.sqlite", database,
                 {"uid": os.geteuid(), "gid": os.getegid(), "mode": "0600"})
    receipt = {"commit": COMMIT, "source_tree": SOURCE_TREE, "target": TARGET,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "before": checked["before"], "postimage": None, "container": checked["anchor"],
               "preflight_health": checked["health"], "logical_agent_config": snapshot,
               "agents_db_backup": {"evidence_only": True, "restored": False,
                                    "sha256": sha256(database), "size": len(database)},
               "status": "captured"}
    save_json(backup / "transaction.json", receipt)
    try:
        health = native.health(); require_zero(health); native.pbx_channels()
        if native.agent_snapshot() != snapshot or fingerprint(root / TARGET) != checked["before"]:
            raise Blocked("pre_stop_state_changed")
        require_container(native, checked["anchor"], running=True); native.stop()
        require_container(native, checked["anchor"], running=False)
        def journal():
            if fingerprint(root / TARGET) != checked["before"]:
                raise Blocked("pre_replace_engine_changed")
        receipt["postimage"] = atomic_bytes(root / TARGET, checked["source"], AFTER, journal)
        save_json(backup / "transaction.json", receipt)
        native.start(); require_container(native, checked["anchor"], running=True)
        native.installed_syntax(); post = native.health(); require_zero(post); native.pbx_channels()
        logical = native.agent_snapshot()
        if logical != snapshot or post.get("config_hash") != checked["health"].get("config_hash"):
            raise Blocked("postflight_logical_config_changed")
        if stable(fingerprint(root / TARGET)) != AFTER:
            raise Blocked("postflight_engine_mismatch")
        receipt.update(status="applied", postflight_health=post)
        save_json(backup / "transaction.json", receipt)
        return backup, receipt
    except BaseException as primary:
        try:
            restore(native, backup, receipt, root)
            receipt["status"] = "rolled_back"
            save_json(backup / "transaction.json", receipt)
        except BaseException as recovery:
            error = Blocked("automatic_rollback_blocked")
            error.backup = str(backup); error.primary_error = type(primary).__name__; error.rollback_error = type(recovery).__name__
            raise error from primary
        raise


def rollback(native, backup, root=LIVE_ROOT):
    safe_path(backup)
    if backup.parent != BACKUP_BASE or stat.S_IMODE(backup.stat().st_mode) != 0o700:
        raise Blocked("backup_path_or_mode_invalid")
    receipt_path = backup / "transaction.json"
    if fingerprint(receipt_path)["mode"] != "0600":
        raise Blocked("receipt_not_protected")
    receipt = json.loads(receipt_path.read_bytes())
    if receipt.get("commit") != COMMIT or receipt.get("source_tree") != SOURCE_TREE or receipt.get("target") != TARGET:
        raise Blocked("receipt_identity_mismatch")
    evidence = receipt.get("agents_db_backup")
    if (not isinstance(evidence, dict) or evidence.get("evidence_only") is not True
            or evidence.get("restored") is not False):
        raise Blocked("database_evidence_contract_mismatch")
    database_path = backup / "agents-db-snapshot.sqlite"
    database_id = fingerprint(database_path)
    if (database_id is None or database_id["mode"] != "0600"
            or database_id["sha256"] != evidence.get("sha256")
            or database_id["size"] != evidence.get("size")):
        raise Blocked("database_evidence_mismatch")
    restore(native, backup, receipt, root)
    receipt["status"] = "rolled_back"; save_json(receipt_path, receipt)
    return {"status": "rolled_back"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--rollback", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.apply or args.rollback) and os.geteuid() != 0:
            raise Blocked("apply_and_rollback_require_root")
        if args.apply or args.rollback:
            safe_path(BACKUP_BASE); BACKUP_BASE.mkdir(parents=True, exist_ok=True)
            fd = os.open(BACKUP_BASE / ".runtime-fbb1e2d.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = ({"status": apply(Native())[1]["status"]} if args.apply
                          else rollback(Native(), args.rollback))
        else:
            checked = check(Native())
            result = {"status": "check_only_pass", "health": checked["health"],
                      "logical_agent_config": checked["logical_agent_config"]}
        print(json.dumps({"commit": COMMIT, **result}, sort_keys=True)); return 0
    except (Blocked, OSError, ValueError, SyntaxError, KeyError, TypeError) as error:
        print(json.dumps({"commit": COMMIT, "status": "blocked",
                          "error": str(error) if isinstance(error, Blocked) else type(error).__name__}, sort_keys=True)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
