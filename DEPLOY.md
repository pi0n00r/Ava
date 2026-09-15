# Accepted Ava Assembly

The current deployment is already installed. Packaging it does not require
another restart, configuration write or call. Keep extension 7's public
receptionist separate from extension 6's full private agent. Native routing,
mailbox authentication and delivery belong to FreePBX/Rita; the relay belongs
to Crustacea. Tessa provides synthesised message audio, not original recordings.

## Verify An Existing Installation

From this kit on the Ava host, use the existing non-mutating installed-state
check at zero calls:

```sh
sudo python3 -B deploy/deploy-ava-prior-message-reference.py --verify-installed
```

It checks the exact accepted two-file postimage, running image, effective
configuration, logical agent records, source mount and authenticated native
channel count. Do not use `--check-only` as a health check: that mode requires
the older activation preimage. No application is authorised by a package file.

## Reconstruct On Replacement Hardware

1. Prepare the documented Debian 13 VM with its NVIDIA GPU and supported driver,
   Docker Engine and Compose, existing host identity and dual-stack networking.
   The private `protected/host-reference/` files record the captured OS, Docker
   settings and interface configuration. Reconcile physical NIC names before
   applying them; never copy another machine's address accidentally. Host and
   FreePBX base setup remain in their separately owned Legible setup guides.
2. Verify the adjacent canonical archive against `SHA256SUMS`, extract its
   `ava/` directory into `/opt/AVA-AI-Voice-Agent-for-Asterisk`, and verify the
   canonical per-file manifest there using `sha256sum -c`.
3. On a NEW installation only, populate that root from
   `protected/runtime-root/`, preserving directory layout. Keep `.env`, managed
   secrets and user configuration private. Restore configuration/data ownership
   to the effective owners recorded in `protected/CAPTURE.json` and container
   mounts in `protected/containers.json`. The deployment actor on the accepted
   host is aimee uid/gid 1001. Do not apply database snapshots over a running
   installation: they are consistent total-loss bootstrap copies, not rollbacks.
4. Read each container's captured Compose file list from its
   `com.docker.compose.project.config_files` label in `protected/containers.json`.
   Run Compose from the original project root with precisely those files, in
   their recorded order. Do not start every historical or example override.
   `protected/images.json` records image IDs, tags, repository digests, startup
   configuration and platform. Retained published image digests may be pulled;
   local-only images require rebuilding from this canonical source and the
   matching component Dockerfile. An image ID is not a registry download URL.
5. Acquire the selected model weights from the retained
   `model-download-sources.json`, verify their declared identities and place
   them at the mounted model paths. Restore managed API credentials before
   starting a provider. Rebuild the local STT/TTS service from this source too:
   the accepted idle-finalisation changes are already in `local_ai_server/`.
   This is not a replay of separate historical patches.
6. Restore Rita/VIP and the matching Crustacea relay from their OWN current
   kits. Retain native voicemail/FreePBX semantics. Do not copy an old relay
   out of a historical Ava kit. Configure the main agent's existing current-call
   tool binding and native context; the current agent database/configuration
   capture contains their accepted settings.
7. Check Compose configuration privately before build/start; its output may
   contain secrets. Start the selected services, then verify source hashes,
   model loading, STT/TTS, the two agent roles, dual-stack paths, native PIN
   ingress and call cleanup. A newly built image has a new identity; do not
   waive an existing-image check or label it the captured binary. Qualify the
   rebuild separately with the retained tests and real incoming/outgoing calls.
8. A memo succeeds only after native IMAP readback proves one correctly
   correlated message and audio attachment. A spoken promise or HTTP 200 alone
   is insufficient. Spoken farewell precedes termination. Latency observations
   do not erase a verified deposit, nor prove a nine-turn conversation.

## Maintenance Boundary

The retained two-file activation helper is the transaction that installed this
repair; its algorithm, exact pre/postimages and tests are retained together.
It does not provision Docker, rewrite the dialplan or restore the database.
Do not replay it on an already-current or mismatched assembly. Preserve accrued
state and use only a matching, explicitly selected recovery checkpoint.

The four payloads and private inputs are verified independently at packaging.
Fresh-host reconstruction and the wider telephony qualification remain separate
from the accepted incoming memo/farewell call. Do not represent packaging as
having performed a cold restore, GPU build or another human acceptance call.
