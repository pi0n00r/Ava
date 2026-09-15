#!/usr/bin/env python3
# AI-NOTICE:License=AGPL-3.0-or-later
"""Capture current private recovery inputs without changing service state."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    spec = importlib.util.spec_from_file_location("accepted_deployer", args.helper)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    native = module.Native()
    before = module.verify_installed(native)
    root = module.LIVE_ROOT.parent
    paths = set(root.glob("docker-compose*.yml"))
    paths.add(root / ".env")
    for folder in ("config", "secrets"):
        paths.update(p for p in (root / folder).rglob("*") if p.is_file())
    paths = {p for p in paths if ".bak" not in p.name and not p.name.endswith(".orig")}
    inventory = {}
    for p in sorted(paths):
        identity = module.fingerprint(p)
        body = p.read_bytes()
        if module.fingerprint(p) != identity:
            raise RuntimeError("configuration_changed_during_capture")
        relative = p.relative_to(root).as_posix()
        target = args.output / "runtime-root" / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(body)
        inventory[relative] = identity
    databases = {}
    for p in sorted((root / "data").rglob("agents.db")):
        relative = p.relative_to(root).as_posix()
        target = args.output / "runtime-root" / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(target) as dest:
                source.backup(dest)
                if dest.execute("pragma integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("database_backup_integrity_failed")
        databases[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    containers = json.loads(native.run(["docker", "inspect", "ai_engine", "admin_ui", "local_ai_server"], "capture_containers"))
    (args.output / "containers.json").write_text(json.dumps(containers, indent=2) + "\n")
    images = list(dict.fromkeys(c["Image"] for c in containers))
    (args.output / "images.json").write_bytes(native.run(["docker", "image", "inspect", *images], "capture_images"))
    host_files = ("/etc/os-release", "/etc/docker/daemon.json", "/etc/network/interfaces")
    for name in host_files:
        p = Path(name)
        if p.is_file():
            target = args.output / "host-reference" / name.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(p.read_bytes())
    source_hashes = {}
    for folder in ("src", "local_ai_server"):
        for p in sorted((root / folder).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                source_hashes[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    live_local = native.run(["docker", "exec", "local_ai_server", "sha256sum", "/app/server.py", "/app/session.py"], "capture_stt_hashes").decode()
    after = module.verify_installed(native)
    if before != after:
        raise RuntimeError("runtime_changed_during_capture")
    for name, identity in inventory.items():
        if module.fingerprint(root / name) != identity:
            raise RuntimeError("configuration_changed_after_capture")
    receipt = {"before": before, "after": after, "configuration": inventory,
               "database_snapshots": databases, "runtime_source_hashes": source_hashes,
               "container_stt_hashes": live_local, "service_changes": False,
               "database_usage": "total-loss bootstrap only; never routine rollback"}
    (args.output / "CAPTURE.json").write_text(json.dumps(receipt, indent=2) + "\n")
    archive = args.output.with_suffix(".tar.gz")
    with tarfile.open(archive, "x:gz") as tar:
        tar.add(args.output, arcname="protected", recursive=True)
    os.chmod(archive, 0o600)
    print(json.dumps({"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "configuration_files": len(inventory), "databases": len(databases),
                      "runtime_source_files": len(source_hashes), "service_changes": False}))


if __name__ == "__main__":
    main()
