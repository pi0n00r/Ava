# Main-agent memo collection without repeated inference

The existing `pbx_message_deposit` tool can start a main-agent memo workflow.
Its model-visible arguments remain `target` and `message`. An empty message
starts collection; a nonempty draft must match a fresh utterance from this
call. The model is selecting an operation, not authorising a write.

After that selection, the existing per-call guard collects the caller's
words, reads back the recipient and exact message, accepts corrections or
cancellation, and requires fresh confirmation. The confirmed operation is
dispatched directly through the existing HTTP executor. A vetted native
receipt supplies the spoken acknowledgement without a model continuation.
Synthetic buffered and streaming tests count one inference across this flow,
and one native request despite a repeated confirmation. Ordinary conversation
and later, unrelated instructions still use the full agent.

Activation requires both trusted transport flags and the native session
context `aimee_main`. Extn 7 retains its existing message-taking behaviour.
No gateway tool narrowing, model substitution, PIN, dialplan, STT, TTS,
FreePBX routing or database change is part of this correction.

The existing deposit saves Tessa-rendered audio of confirmed text in native
voicemail. It is not an original recording of the caller. Original-audio
preservation and measured human-call latency are separate acceptance gates.

Deploy the matching relay descriptor/validator and Ava guard/engine together
at zero active calls, after retaining exact preimages. The existing guarded
installer preserves configuration and agent records and restores only its
own changed source files on failure. Do not roll back a VM or its database.
Retain the preceding Extn 7 history repair and all other accepted source.

This source document defines the correction, not a claim of a new native
IMAP receipt or complete telephony acceptance. The prior Extn 6 call said
"Noted" without saving anything; that observation remains a failed deposit.
