from prometheus_client import REGISTRY


def _labelnames(metric) -> tuple:
    return tuple(getattr(metric, "_labelnames", ()) or ())


def test_no_call_id_labelnames_on_ai_agent_metrics():
    # Spot-check key modules where call_id cardinality previously existed.
    from src.core import vad_manager
    from src.core import conversation_coordinator
    from src.core import streaming_playback_manager
    from src import engine

    assert "call_id" not in _labelnames(vad_manager._VAD_FRAMES_TOTAL)
    assert "call_id" not in _labelnames(vad_manager._VAD_CONFIDENCE_HISTOGRAM)
    assert "call_id" not in _labelnames(vad_manager._VAD_ADAPTIVE_THRESHOLD)

    assert "call_id" not in _labelnames(conversation_coordinator._TTS_GATING_GAUGE)
    assert "call_id" not in _labelnames(conversation_coordinator._AUDIO_CAPTURE_GAUGE)
    assert "call_id" not in _labelnames(conversation_coordinator._CONVERSATION_STATE_GAUGE)
    assert "call_id" not in _labelnames(conversation_coordinator._BARGE_IN_COUNTER)

    assert "call_id" not in _labelnames(streaming_playback_manager._STREAMING_ACTIVE_GAUGE)
    assert "call_id" not in _labelnames(streaming_playback_manager._STREAM_STARTED_TOTAL)
    assert "call_id" not in _labelnames(streaming_playback_manager._STREAM_END_REASON_TOTAL)

    assert "call_id" not in _labelnames(engine._CALL_DURATION)


def test_no_call_id_labels_emitted_for_ai_agent_metric_families():
    for family in REGISTRY.collect():
        if not family.name.startswith("ai_agent_"):
            continue
        for sample in family.samples:
            assert "call_id" not in (sample.labels or {})

