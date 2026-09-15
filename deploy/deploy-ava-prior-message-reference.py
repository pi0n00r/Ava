#!/usr/bin/env python3
# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Paired main memo activation; database/config captures are evidence only."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

COMMIT = "27b936e93b61b35981a411eaede2fc4e42e461e7"
SOURCE_TREE = "308dc8939d59887c3b75ab7b0233a32cd7ea0b4c"
IMAGE = "sha256:91ae246a07be78ff38ac4d5e95bcaa1deedac17bbaa1ae96c0e379455d04f2c7"
LIVE_ROOT = Path("/opt/AVA-AI-Voice-Agent-for-Asterisk/src")
CANDIDATE_ROOT = Path(__file__).resolve().parent.parent / "src"
BACKUP_BASE = Path("/home/aimee/.local/share/ava-rollback")
TARGETS = ("core/pipeline_message_deposit.py", "engine.py")
RUNTIME_OWNER = {"uid": 1001, "gid": 1001}
STAGE_OWNER = {"uid": 1001, "gid": 1001}
SONYHAL_SOURCE_OWNER = {"uid": 1000, "gid": 1000}
BEFORE = {
    "core/pipeline_message_deposit.py": {
        "sha256": "f1b2ce1fece75c6c0870c82c8266f8c82a7fc1df8aece96d350af1bc0f07e0be",
        "size": 33599,
        "mode": "0644",
        "uid": 1001,
        "gid": 1001
    },
    "engine.py": {
        "sha256": "1e382013e719b1eb73aa163f2e462d3ea71a32bf1c374335009506e178ad1780",
        "size": 1122150, "mode": "0644", "uid": 1001, "gid": 1001
    }
}
AFTER = {
    "core/pipeline_message_deposit.py": {
        "sha256": "d9cf660a226d4e2c2a2e1ee2a8b4ee9a2cd89d7f84a2571a978bd3a6aa445693",
        "size": 36126,
        "mode": "0644",
        "uid": 1001,
        "gid": 1001
    },
    "engine.py": {
        "sha256": "5ed471f1a0395595c22f112b8d43b574aeb698105fa03ea2abd63cdd9a8d1c4e",
        "size": 1124606, "mode": "0644", "uid": 1001, "gid": 1001
    }
}
STAGED = {name:{**value,**STAGE_OWNER} for name,value in AFTER.items()}
PROTECTED_PATHS = (".env", "config/ai-agent.yaml", "config/ai-agent.local.yaml", "docker-compose.yml")
LOCK_NAME = ".runtime-message-guard-52768f2.lock"

class Blocked(Exception):
    pass

def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise Blocked("nonabsolute_or_parent_path")
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
    fields = ("st_dev","st_ino","st_size","st_mtime_ns","st_mode","st_uid","st_gid")
    if tuple(getattr(before,k) for k in fields) != tuple(getattr(after,k) for k in fields):
        raise Blocked("concurrent_read_change")
    return {"sha256":sha256(data),"size":after.st_size,
            "mode":format(stat.S_IMODE(after.st_mode),"04o"),"uid":after.st_uid,"gid":after.st_gid,
            "mtime_ns":str(after.st_mtime_ns),"dev":after.st_dev,"ino":after.st_ino}

def stable(identity):
    if identity is None:
        return None
    return {k:identity[k] for k in ("sha256","size","mode","uid","gid")}

def identities(root):
    return {name:fingerprint(root/name) for name in TARGETS}

def protected_configuration(project, capture=False):
    result, payloads = {}, {}
    for name in PROTECTED_PATHS:
        path = project/name
        identity = fingerprint(path)
        result[name] = identity
        if capture and identity is not None:
            body = path.read_bytes()
            if sha256(body) != identity["sha256"] or fingerprint(path) != identity:
                raise Blocked("protected_capture_changed")
            payloads[name] = body
    return (result,payloads) if capture else result

def prepare_bytes(path,data,metadata):
    safe_path(path)
    fd,temporary = tempfile.mkstemp(prefix=".ava-message-guard-52768f2-",dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data); stream.flush()
            os.fchown(stream.fileno(),metadata["uid"],metadata["gid"])
            os.fchmod(stream.fileno(),int(metadata["mode"],8))
            if metadata.get("mtime_ns") is not None:
                value = int(metadata["mtime_ns"])
                os.utime(stream.fileno(),ns=(value,value))
            os.fsync(stream.fileno())
        return temporary,fingerprint(temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

def replace_prepared(temporary,path,expected):
    if fingerprint(temporary) != expected:
        raise Blocked("prepared_file_changed")
    safe_path(path)
    os.replace(temporary,path)
    directory = os.open(path.parent,os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    return fingerprint(path)

def atomic_bytes(path,data,metadata):
    temporary,identity = prepare_bytes(path,data,metadata)
    try: return replace_prepared(temporary,path,identity)
    finally: temporary.unlink(missing_ok=True)

def private_metadata():
    return {"uid":os.geteuid(),"gid":os.getegid(),"mode":"0600"}

def save_json(path,value):
    atomic_bytes(path,(json.dumps(value,indent=2,sort_keys=True)+"\n").encode(),private_metadata())

def require_zero(health):
    if (not isinstance(health,dict) or health.get("status") != "healthy" or
        health.get("ari_connected") is not True):
        raise Blocked("health_or_ari_not_ready")
    if any(type(health.get(k)) is not int or health[k] != 0
           for k in ("active_calls","active_sessions","asterisk_channels")):
        raise Blocked("quiescence_unavailable_or_active")

def require_pbx(native):
    result = native.pbx_channels()
    if (not isinstance(result,dict) or result.get("authenticated") is not True or
        result.get("operation") != "GET /ari/channels" or
        type(result.get("channels")) is not int or result["channels"] != 0):
        raise Blocked("native_pbx_zero_unavailable_or_active")
    return result

def require_container(native,anchor=None,running=None):
    value = native.inspect()
    if anchor and any(value[k] != anchor[k] for k in ("id","image","mounts_sha256")):
        raise Blocked("container_identity_changed")
    if running is not None and value["running"] is not running:
        raise Blocked("container_running_state_mismatch")
    return value

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
            # Docker may reorder .Mounts; identity includes every field and duplicate.
            mount_set = sorted(value["mounts"],key=lambda m:(
                m.get("Destination",""),m.get("Source",""),m.get("Type",""),
                json.dumps(m,sort_keys=True,separators=(",",":"))))
            mounts_digest = sha256(json.dumps(mount_set,sort_keys=True,separators=(",",":")).encode())
            return {**{k:value[k] for k in ("id","image","running")},"mounts_sha256":mounts_digest}
        except (KeyError, TypeError, ValueError):
            raise Blocked("container_inspect_malformed") from None

    def syntax(self, name, source):
        self.run(["docker", "exec", "-i", "-e", "PYTHONDONTWRITEBYTECODE=1", "ai_engine",
                  "python", "-B", "-c", "import sys;compile(sys.stdin.buffer.read(),"+repr(name)+",'exec')"],
                 "candidate_container_syntax", body=source)

    def installed_syntax(self):
        code = "from pathlib import Path\n"
        for name in TARGETS:
            code += "compile(Path("+repr("/app/src/"+name)+").read_bytes(),"+repr(name)+",'exec')\n"
        self.run(["docker","exec","-i","-e","PYTHONDONTWRITEBYTECODE=1","ai_engine","python","-B","-"],
                 "installed_container_syntax",body=code.encode())

    def runtime_files(self, expected):
        code = r"""import hashlib,json,os,stat
from pathlib import Path
result={}
for name in """+repr(TARGETS)+r""":
 p=Path('/app/src')/name;s=p.stat();b=p.read_bytes()
 result[name]={'sha256':hashlib.sha256(b).hexdigest(),'size':s.st_size,
  'uid':s.st_uid,'gid':s.st_gid,'mode':format(stat.S_IMODE(s.st_mode),'04o')}
print(json.dumps(result,sort_keys=True))"""
        raw=self.run(["docker","exec","-i","ai_engine","python","-B","-"],
                     "native_source_bind_readback",body=code.encode(),timeout=15)
        try:
            actual=json.loads(raw)
            if actual != {name:stable(expected[name]) for name in TARGETS}:
                raise Blocked("container_runtime_source_mismatch")
            return actual
        except (TypeError,ValueError):
            raise Blocked("container_runtime_source_malformed") from None

    def configuration(self, include_capture=False):
        return protected_configuration(LIVE_ROOT.parent,include_capture)

    def stop(self):
        # Docker owns the bounded 30-second stop grace period. Do not add an
        # outer client timeout: an ambiguous client timeout must never trigger
        # a second mutating command while the daemon may still be completing it.
        self.run(["docker", "stop", "--time", "30", "ai_engine"],
                 "container_stop", timeout=None)

    def start(self):
        # Likewise, wait for the one requested start to return. Read-only
        # health and identity probes remain independently bounded.
        self.run(["docker", "start", "ai_engine"], "container_start", timeout=None)

    def agent_snapshot(self, include_backup=False):
        code = r'''import base64,hashlib,json,sqlite3,tempfile,os
from src.core.agent_store import EngineAgentStore
p=EngineAgentStore().db_path
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
                evidence = {"sha256": value.pop("backup_sha256"),
                            "size": value.pop("backup_size")}
                logical = {k: value[k] for k in ("sha256", "agent_count", "columns")}
                return logical, data, evidence
            return {k: value[k] for k in ("sha256", "agent_count", "columns")}
        except (KeyError, TypeError, ValueError):
            raise Blocked("logical_agent_snapshot_malformed") from None

    def health(self):
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen("http://localhost:15000/health", timeout=2) as response:
                    value = json.loads(response.read(65537))
                if (response.status == 200 and value.get("status") == "healthy"
                        and value.get("ari_connected") is True
                        and all(type(value.get(k)) is int for k in ("active_calls", "active_sessions", "asterisk_channels"))):
                    return {k: value.get(k) for k in ("status", "ari_connected", "active_calls", "active_sessions",
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
 rows=json.loads(r.read(1048577))
 if r.status != 200 or not isinstance(rows,list): raise ValueError("pbx_readback_shape")
print(json.dumps({"authenticated":True,"operation":"GET /ari/channels","channels":len(rows)}))'''
        raw = self.run(["docker", "exec", "-i", "ai_engine", "python", "-B", "-"],
                       "native_pbx_readback", body=code.encode(), timeout=10)
        try:
            value = json.loads(raw)
            if (not isinstance(value,dict) or value.get("authenticated") is not True or
                value.get("operation") != "GET /ari/channels" or
                type(value.get("channels")) is not int or value["channels"] != 0):
                raise ValueError
            return value
        except (TypeError, ValueError):
            raise Blocked("native_pbx_zero_unavailable_or_active") from None


def validate_stage(candidate):
    safe_path(candidate)
    directory = candidate.stat()
    if (not stat.S_ISDIR(directory.st_mode) or
        {"uid":directory.st_uid,"gid":directory.st_gid} != STAGE_OWNER or
        stat.S_IMODE(directory.st_mode) != 0o700):
        raise Blocked("candidate_stage_owner_or_mode")
    result = {}
    for name in TARGETS:
        identity = fingerprint(candidate/name)
        if stable(identity) != STAGED[name]:
            raise Blocked("candidate_mismatch:"+name)
        body = (candidate/name).read_bytes()
        if fingerprint(candidate/name) != identity or sha256(body) != identity["sha256"]:
            raise Blocked("candidate_changed_during_capture")
        compile(body,name,"exec")
        result[name] = body
    return result

def check(native,root=LIVE_ROOT,candidate=CANDIDATE_ROOT):
    before = identities(root)
    if any(stable(before[name]) != BEFORE[name] for name in TARGETS):
        raise Blocked("live_preimage_mismatch")
    sources = validate_stage(candidate)
    anchor = require_container(native,running=True)
    for name in TARGETS: native.syntax(name,sources[name])
    health = native.health(); require_zero(health); require_pbx(native)
    logical = native.agent_snapshot()
    configuration = native.configuration()
    native.runtime_files(before)
    if identities(root) != before or native.configuration() != configuration:
        raise Blocked("live_state_changed_during_check")
    require_container(native,anchor,running=True)
    return {"before":before,"anchor":anchor,"health":health,
            "logical_agent_config":logical,"configuration":configuration,"sources":sources}

def verify_installed(native,root=LIVE_ROOT):
    installed = identities(root)
    if any(stable(installed[name]) != AFTER[name] for name in TARGETS):
        raise Blocked("installed_runtime_state_mismatch")
    anchor = require_container(native,running=True)
    native.installed_syntax()
    health = native.health(); require_zero(health); require_pbx(native)
    if not isinstance(health.get("config_hash"),str) or not health["config_hash"]:
        raise Blocked("installed_config_hash_unavailable")
    logical = native.agent_snapshot()
    configuration = native.configuration()
    native.runtime_files(installed)
    final_health = native.health(); require_zero(final_health); require_pbx(native)
    if final_health.get("config_hash") != health["config_hash"]:
        raise Blocked("installed_config_hash_changed")
    if identities(root) != installed:
        raise Blocked("installed_runtime_changed_during_verify")
    if native.configuration() != configuration:
        raise Blocked("installed_protected_config_changed")
    if native.agent_snapshot() != logical:
        raise Blocked("installed_logical_config_changed")
    require_container(native,anchor,running=True)
    return {"installed":installed,"anchor":anchor,"health":final_health,
            "logical_agent_config":logical,"configuration":configuration}

def target_state(identity,row):
    if identity == row["before"]: return "before"
    for key in ("postimage","prepared","restored","restore_prepared"):
        if row.get(key) is not None and identity == row[key]: return key
    raise Blocked("runtime_file_conflict")

def require_state(root,receipt):
    return {name:target_state(fingerprint(root/name),receipt["files"][name]) for name in TARGETS}

def validate_capture(backup,receipt):
    if (not isinstance(receipt.get("configuration"),dict) or
        set(receipt["configuration"]) != set(PROTECTED_PATHS)):
        raise Blocked("protected_config_capture_paths_mismatch")
    for name in TARGETS:
        p = backup/"runtime-before"/name
        identity = fingerprint(p)
        before = receipt["files"][name]["before"]
        if (identity is None or identity["mode"] != "0600" or
            identity["uid"] != os.geteuid() or identity["gid"] != os.getegid() or
            identity["sha256"] != before["sha256"] or identity["size"] != before["size"]):
            raise Blocked("runtime_backup_mismatch")
    evidence = receipt.get("agents_db_backup",{})
    identity = fingerprint(backup/"evidence/agents-db-snapshot.sqlite")
    if (evidence.get("evidence_only") is not True or evidence.get("restored") is not False or
        identity is None or identity["mode"] != "0600" or
        identity["uid"] != os.geteuid() or identity["gid"] != os.getegid() or
        identity["sha256"] != evidence.get("sha256") or identity["size"] != evidence.get("size")):
        raise Blocked("database_evidence_contract_mismatch")
    for name,original in receipt["configuration"].items():
        if original is not None:
            identity = fingerprint(backup/"evidence/protected-config"/name)
            if (identity is None or identity["mode"] != "0600" or identity["uid"] != os.geteuid() or
                identity["gid"] != os.getegid() or identity["sha256"] != original["sha256"] or
                identity["size"] != original["size"]):
                raise Blocked("protected_config_evidence_mismatch")

def postflight(native,receipt,root,expected):
    require_container(native,receipt["container"],running=True)
    native.installed_syntax()
    health = native.health(); require_zero(health); require_pbx(native)
    native.runtime_files(expected)
    if any(stable(fingerprint(root/name)) != stable(expected[name]) for name in TARGETS):
        raise Blocked("postflight_runtime_files_mismatch")
    if native.configuration() != receipt["configuration"]:
        raise Blocked("protected_config_changed")
    if (native.agent_snapshot() != receipt["logical_agent_config"] or
        health.get("config_hash") != receipt["preflight_health"].get("config_hash")):
        raise Blocked("logical_config_changed")
    return health

def restore(native,backup,receipt,root=LIVE_ROOT):
    validate_capture(backup,receipt)
    states = require_state(root,receipt)
    anchor = require_container(native,receipt["container"])
    if native.configuration() != receipt["configuration"]:
        raise Blocked("protected_config_changed_before_restore")
    already = all(states[name] in {"before","restored","restore_prepared"} for name in TARGETS)
    if already and anchor["running"]:
        return postflight(native,receipt,root,{n:receipt["files"][n]["before"] for n in TARGETS})
    if anchor["running"]:
        health = native.health(); require_zero(health); require_pbx(native)
        if (native.agent_snapshot() != receipt["logical_agent_config"] or
            health.get("config_hash") != receipt["preflight_health"].get("config_hash")):
            raise Blocked("logical_config_changed_before_restore")
        if native.configuration() != receipt["configuration"]:
            raise Blocked("protected_config_changed_before_restore")
        require_state(root,receipt)
        require_container(native,receipt["container"],running=True)
        native.stop()
    require_container(native,receipt["container"],running=False)
    states = require_state(root,receipt)
    if native.configuration() != receipt["configuration"]:
        raise Blocked("protected_config_changed_before_restore")
    for name in TARGETS:
        if states[name] in {"before","restored","restore_prepared"}:
            continue
        row = receipt["files"][name]
        source = backup/"runtime-before"/name
        temporary,prepared = prepare_bytes(root/name,source.read_bytes(),row["before"])
        try:
            require_state(root,receipt)
            if native.configuration() != receipt["configuration"]:
                raise Blocked("protected_config_changed_before_restore")
            if stable(prepared) != stable(row["before"]) or prepared["mtime_ns"] != row["before"]["mtime_ns"]:
                raise Blocked("prepared_restore_mismatch")
            row["restore_prepared"] = prepared
            save_json(backup/"transaction.json",receipt)
            row["restored"] = replace_prepared(temporary,root/name,prepared)
            save_json(backup/"transaction.json",receipt)
        finally: temporary.unlink(missing_ok=True)
    receipt["rollback_runtime_files_restored"] = True
    save_json(backup/"transaction.json",receipt)
    if native.configuration() != receipt["configuration"]:
        raise Blocked("protected_config_changed_before_restart")
    native.start()
    return postflight(native,receipt,root,{n:receipt["files"][n]["before"] for n in TARGETS})

def apply(native,root=LIVE_ROOT,candidate=CANDIDATE_ROOT,base=BACKUP_BASE,backup=None):
    checked = check(native,root,candidate)
    safe_path(base); base.mkdir(parents=True,exist_ok=True)
    if backup is None:
        backup = Path(tempfile.mkdtemp(prefix="runtime-message-guard-52768f2-",dir=base))
    else:
        backup = Path(backup)
        safe_path(backup)
        if backup.parent != base: raise Blocked("backup_outside_base")
        backup.mkdir(mode=0o700,exist_ok=False)
    backup.chmod(0o700)
    (backup/"runtime-before/core").mkdir(parents=True,mode=0o700)
    (backup/"runtime-before").chmod(0o700)
    (backup/"runtime-before/tools").mkdir(mode=0o700)
    (backup/"runtime-before/tools/http").mkdir(mode=0o700)
    (backup/"evidence").mkdir(mode=0o700)
    snapshot,database,database_evidence = native.agent_snapshot(include_backup=True)
    configuration,payloads = native.configuration(include_capture=True)
    if snapshot != checked["logical_agent_config"] or configuration != checked["configuration"]:
        raise Blocked("snapshot_changed_before_capture")
    if identities(root) != checked["before"]:
        raise Blocked("runtime_changed_before_capture")
    for name in TARGETS:
        body = (root/name).read_bytes()
        if sha256(body) != checked["before"][name]["sha256"] or fingerprint(root/name) != checked["before"][name]:
            raise Blocked("runtime_capture_changed")
        atomic_bytes(backup/"runtime-before"/name,body,private_metadata())
    atomic_bytes(backup/"evidence/agents-db-snapshot.sqlite",database,private_metadata())
    for name,body in payloads.items():
        path = backup/"evidence/protected-config"/name
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        for parent in path.parents:
            if parent == backup: break
            parent.chmod(0o700)
        atomic_bytes(path,body,private_metadata())
    receipt = {"commit":COMMIT,"source_tree":SOURCE_TREE,"targets":list(TARGETS),
        "created_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "files":{n:{"before":checked["before"][n],"prepared":None,"postimage":None,
                    "restore_prepared":None,"restored":None} for n in TARGETS},
        "container":checked["anchor"],"preflight_health":checked["health"],
        "logical_agent_config":snapshot,"configuration":configuration,
        "agents_db_backup":{"evidence_only":True,"restored":False,**database_evidence},
        "config_capture_evidence_only":True,"status":"captured","phase":"captured"}
    save_json(backup/"transaction.json",receipt)
    prepared = {}
    try:
        health = native.health(); require_zero(health); require_pbx(native)
        if (native.agent_snapshot() != snapshot or native.configuration() != configuration or
            identities(root) != checked["before"]):
            raise Blocked("pre_stop_state_changed")
        require_container(native,checked["anchor"],running=True)
        receipt["phase"] = "stop"; save_json(backup/"transaction.json",receipt)
        native.stop(); require_container(native,checked["anchor"],running=False)
        if native.configuration() != configuration: raise Blocked("protected_config_changed_after_stop")
        for name in TARGETS:
            temporary,identity = prepare_bytes(root/name,checked["sources"][name],AFTER[name])
            prepared[name] = temporary
            if stable(identity) != AFTER[name]: raise Blocked("prepared_candidate_mismatch")
            receipt["files"][name]["prepared"] = identity
        receipt["phase"] = "prepared"; save_json(backup/"transaction.json",receipt)
        for name in TARGETS:
            states = require_state(root,receipt)
            if states[name] != "before" or native.configuration() != configuration:
                raise Blocked("pre_replace_state_changed")
            require_container(native,checked["anchor"],running=False)
            receipt["phase"] = "replace:"+name; save_json(backup/"transaction.json",receipt)
            row = receipt["files"][name]
            row["postimage"] = replace_prepared(prepared[name],root/name,row["prepared"])
            save_json(backup/"transaction.json",receipt)
        receipt["phase"] = "start"; save_json(backup/"transaction.json",receipt)
        native.start()
        receipt["phase"] = "postflight"
        post = postflight(native,receipt,root,AFTER)
        receipt.update(status="applied",phase="complete",postflight_health=post)
        save_json(backup/"transaction.json",receipt)
        return backup,receipt
    except BaseException as primary:
        receipt["primary_failure"] = {"phase":receipt["phase"],
            "category":str(primary) if isinstance(primary,Blocked) else type(primary).__name__}
        try:
            restore(native,backup,receipt,root)
            receipt.update(status="rolled_back",rollback_validation="pass")
            save_json(backup/"transaction.json",receipt)
        except BaseException as recovery:
            receipt.update(status="rollback_blocked",rollback_validation="blocked",
                rollback_failure=str(recovery) if isinstance(recovery,Blocked) else type(recovery).__name__)
            try: save_json(backup/"transaction.json",receipt)
            except (OSError,Blocked): pass
            error = Blocked("automatic_rollback_blocked")
            error.backup = str(backup)
            raise error from None
        if isinstance(primary,Blocked):
            primary.backup = str(backup)
        raise
    finally:
        for temporary in prepared.values(): temporary.unlink(missing_ok=True)

def rollback(native,backup,root=LIVE_ROOT,base=BACKUP_BASE):
    backup = Path(backup)
    safe_path(backup)
    s = backup.stat()
    if (backup.parent != base or stat.S_IMODE(s.st_mode) != 0o700 or
        s.st_uid != os.geteuid() or s.st_gid != os.getegid()):
        raise Blocked("backup_path_owner_or_mode_invalid")
    identity = fingerprint(backup/"transaction.json")
    if (identity is None or identity["mode"] != "0600" or
        identity["uid"] != os.geteuid() or identity["gid"] != os.getegid()):
        raise Blocked("receipt_not_protected")
    receipt = json.loads((backup/"transaction.json").read_bytes())
    if (receipt.get("commit") != COMMIT or receipt.get("source_tree") != SOURCE_TREE or
        receipt.get("targets") != list(TARGETS) or set(receipt.get("files",{})) != set(TARGETS) or
        receipt.get("config_capture_evidence_only") is not True):
        raise Blocked("receipt_identity_mismatch")
    for name in TARGETS:
        if stable(receipt["files"][name]["before"]) != BEFORE[name]:
            raise Blocked("receipt_preimage_mismatch")
        for key in ("prepared","postimage"):
            if receipt["files"][name].get(key) is not None and stable(receipt["files"][name][key]) != AFTER[name]:
                raise Blocked("receipt_postimage_mismatch")
        for key in ("restored","restore_prepared"):
            restored = receipt["files"][name].get(key)
            if restored is not None and (stable(restored) != BEFORE[name] or
                restored["mtime_ns"] != receipt["files"][name]["before"]["mtime_ns"]):
                raise Blocked("receipt_restore_identity_mismatch")
    result = restore(native,backup,receipt,root)
    if receipt["status"] != "rolled_back":
        receipt.update(status="rolled_back",rollback_validation="pass",rollback_health=result)
        save_json(backup/"transaction.json",receipt)
    return {"status":"rolled_back","backup":str(backup)}

def with_lock(operation,base=BACKUP_BASE):
    safe_path(base); base.mkdir(parents=True,exist_ok=True)
    path = base/LOCK_NAME
    safe_path(path)
    fd = os.open(path,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as lock:
        s = os.fstat(lock.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or
            s.st_uid != os.geteuid() or s.st_gid != os.getegid() or stat.S_IMODE(s.st_mode) != 0o600):
            raise Blocked("lock_owner_mode_or_type")
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return operation()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-only",action="store_true")
    modes.add_argument("--verify-installed",action="store_true")
    modes.add_argument("--apply",action="store_true")
    modes.add_argument("--rollback",type=Path)
    parser.add_argument("--backup",type=Path,help="exclusive new backup directory for --apply")
    args = parser.parse_args(argv)
    try:
        if COMMIT is None or SOURCE_TREE is None: raise Blocked("source_binding_pending")
        if args.backup and not args.apply: raise Blocked("backup_argument_requires_apply")
        if (args.apply or args.rollback) and os.geteuid() != 0:
            raise Blocked("apply_and_rollback_require_root")
        native = Native()
        if args.check_only:
            checked = check(native)
            result = {"status":"check_only_pass","health":checked["health"],
                      "logical_agent_config":checked["logical_agent_config"],
                      "configuration":checked["configuration"]}
        elif args.verify_installed:
            verified = verify_installed(native)
            result = {"status":"installed_verify_pass","health":verified["health"],
                      "logical_agent_config":verified["logical_agent_config"],
                      "configuration":verified["configuration"],
                      "installed":verified["installed"]}
        else:
            def operation():
                if args.apply:
                    backup,receipt = apply(native,backup=args.backup)
                    return {"status":receipt["status"],"backup":str(backup)}
                return rollback(native,args.rollback)
            result = with_lock(operation)
        print(json.dumps({"commit":COMMIT,**result},sort_keys=True)); return 0
    except (Blocked,OSError,ValueError,SyntaxError,KeyError,TypeError) as error:
        print(json.dumps({"commit":COMMIT,"status":"blocked",
            "error":str(error) if isinstance(error,Blocked) else type(error).__name__,
            **({"backup":error.backup} if hasattr(error,"backup") else {})},sort_keys=True)); return 1

if __name__ == "__main__":
    raise SystemExit(main())
