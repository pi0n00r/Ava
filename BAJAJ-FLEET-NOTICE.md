<!--
AI-NOTICE:Schema-Version=0.1
AI-NOTICE:License=AGPL-3.0-or-later
AI-NOTICE:Project=Ava
AI-NOTICE:Scope=Fleet-authored changes; upstream MIT material retains its notices
-->

# Maintained Fork: Licence And Source Boundaries

This repository is the maintained Bajaj fleet fork of Ava. The supplied upstream
`LICENSE` is retained byte-for-byte under MIT. Fleet-authored changes and added
tests, deployment helpers, and documentation are AGPL-3.0-or-later under their
AI-NOTICE headers and `LICENSE-AGPL-3.0.txt`. These notices supplement rather
than replace the upstream MIT licence.

The initial Git history was reconstructed from the exact supplied live-root
capture because the corresponding upstream commit history was unavailable.
Commit `7dcb5b1` records that boundary. Subsequent fleet commits are ordinary,
reviewable source changes. Accepted work currently includes call capture and
cancellation fixes, native greeting and wait-media behaviour, confirmed message
deposit dispatch, prepared outbound speech, Unicode format-control handling,
caller-end/gratitude behaviour, DTMF log redaction, and agent-configured HTTP
tool advertisement. The root README/CHANGELOG version remains the upstream
application version; `FLEET-RELEASE.json` identifies the distinct maintained
fork release and never implies an upstream provenance that has not been proved.

Production installation is built from a versioned fork source release. The
`deploy/deploy-ava-http-advertisement.py` transaction exists for the currently
reviewed one-file transition and recovery only; it is not a permanent patch
replay mechanism or a substitute for the maintained source release.
