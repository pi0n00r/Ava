#!/usr/bin/env python3
# AI-NOTICE:License=AGPL-3.0-or-later
"""Build and independently read back the four Ava release payloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def members(path, root):
    result = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts, "invalid_archive_path")
            require(name.parts[0] == root and (member.isfile() or member.isdir()), "unexpected_archive_entry")
            if member.isfile():
                relative = str(name.relative_to(root))
                require(relative not in result, "duplicate_archive_file")
                result[relative] = (archive.extractfile(member).read(), member.mode)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--capture-sha256", required=True)
    parser.add_argument("--model-sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    def git(*parts):
        return subprocess.check_output(["git", "-C", str(args.repo), *parts])
    require(not git("status", "--porcelain").strip(), "source_worktree_not_clean")
    commit = git("rev-parse", "HEAD").decode().strip()
    version = git("describe", "--tags", "--always").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    require(digest(args.capture.read_bytes()) == args.capture_sha256, "capture_hash_mismatch")
    captured = members(args.capture, "protected")
    capture = json.loads(captured["CAPTURE.json"][0])
    require(capture["before"] == capture["after"] and capture["service_changes"] is False,
            "capture_not_stable")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    slug = "ava-pi0n00r-" + version
    canonical = args.output / (slug + "-canonical.tar.gz")
    canonical.write_bytes(git("archive", "--format=tar.gz", "--prefix=ava/", commit))
    source = members(canonical, "ava")
    mismatches = [name for name, value in capture["runtime_source_hashes"].items()
                  if name.startswith("src/") and (name not in source or digest(source[name][0]) != value)]
    require(not mismatches, "live_src_mismatch:" + ",".join(mismatches))
    for line in capture["container_stt_hashes"].splitlines():
        value, path = line.split(None, 1)
        name = "local_ai_server/" + Path(path).name
        require(name in source and digest(source[name][0]) == value, "live_stt_source_mismatch:" + name)
    secret_values = set()
    for name, (body, _) in captured.items():
        if name.endswith(".env"):
            for line in body.decode("utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                value = value.strip().strip("\"'")
                if sep and re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", key, re.I) and len(value) >= 12:
                    secret_values.add(value.encode())
    leaks = [name for name, (body, _) in source.items() if any(value in body for value in secret_values)]
    require(not leaks, "private_value_in_canonical_source:" + ",".join(leaks))
    manifest = args.output / (slug + "-canonical.sha256")
    manifest.write_text("".join(digest(body) + "  ./" + name + "\n"
                                for name, (body, _) in sorted(source.items())))
    kit = args.output / "ava"
    kit.mkdir(mode=0o700)
    for name, (body, mode) in source.items():
        target = kit / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(mode & 0o755)
    for name, (body, _) in captured.items():
        target = kit / "protected" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(body)
        target.chmod(0o600)
    (kit / "model-download-sources.json").write_bytes(args.model_sources.read_bytes())
    identity = {"source_version": version, "git_commit": commit, "source_tree": tree,
                "runtime_commit": "27b936e93b61b35981a411eaede2fc4e42e461e7",
                "runtime_source_tree": git("rev-parse", "HEAD:src").decode().strip(),
                "live_source_matched": True, "live_stt_matched": True,
                "capture_sha256": args.capture_sha256,
                "container_image": capture["before"]["anchor"]["image"],
                "memo_human_grade": "GREEN", "whole_assembly_grade": "pending",
                "fresh_host_reconstruction": "not_exercised",
                "private_env_values_checked": len(secret_values)}
    (kit / "SOURCE-IDENTITY.json").write_text(json.dumps(identity, indent=2) + "\n")
    inventory = {}
    for p in sorted(kit.rglob("*")):
        require(not p.is_symlink(), "kit_symlink")
        if p.is_file():
            name = p.relative_to(kit).as_posix()
            inventory[name] = {"sha256": digest(p.read_bytes()), "mode": p.stat().st_mode & 0o777,
                               "size": p.stat().st_size}
    sums = "".join(row["sha256"] + "  ./" + name + "\n" for name, row in inventory.items())
    (kit / "KIT-SHA256SUMS").write_text(sums)
    inventory["KIT-SHA256SUMS"] = {"sha256": digest(sums.encode()), "mode": 0o600,
                                     "size": len(sums.encode())}
    (args.output / "KIT-INVENTORY.json").write_text(json.dumps(inventory, indent=2) + "\n")
    skeleton = args.output / ("v" + version + "-skeleton.tar.gz")
    with tarfile.open(skeleton, "x:gz") as tar:
        tar.add(kit, arcname="ava")
    readback = members(skeleton, "ava")
    require({n: {"sha256": digest(b), "mode": m, "size": len(b)} for n, (b, m) in readback.items()}
            == inventory, "skeleton_kit_bytes_or_modes_mismatch")
    require(members(canonical, "ava") == source, "canonical_readback_changed")
    (args.output / "SOURCE-IDENTITY.json").write_text(json.dumps(identity, indent=2) + "\n")
    artifacts = [canonical, manifest, skeleton, args.output / "KIT-INVENTORY.json", args.output / "SOURCE-IDENTITY.json"]
    (args.output / "SHA256SUMS").write_text("".join(digest(p.read_bytes()) + "  " + p.name + "\n" for p in artifacts))
    print(json.dumps({**identity, "canonical_files": len(source), "kit_files": len(inventory),
                      "kit_to_skeleton": "pass", "artifacts": {p.name: digest(p.read_bytes()) for p in artifacts}}))


if __name__ == "__main__":
    main()
