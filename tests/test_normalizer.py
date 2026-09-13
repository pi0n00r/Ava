import math
import array
import pytest

from src.core.streaming_playback_manager import StreamingPlaybackManager


def _rms_pcm16(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    buf = array.array('h')
    buf.frombytes(pcm)
    if len(buf) == 0:
        return 0.0
    acc = 0.0
    for s in buf:
        acc += float(s) * float(s)
    return math.sqrt(acc / float(len(buf)))


def _make_sine_pcm16(amplitude: int, samples: int, freq: float = 440.0, rate: int = 8000) -> bytes:
    # Simple 16-bit mono sine wave
    buf = array.array('h')
    two_pi_f = 2.0 * math.pi * freq
    for n in range(samples):
        t = n / float(rate)
        val = int(round(amplitude * math.sin(two_pi_f * t)))
        # Clip to int16
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        buf.append(val)
    return buf.tobytes()


def _make_manager():
    class Dummy:
        pass
    # Only using _apply_normalizer; other deps are not exercised
    return StreamingPlaybackManager(
        session_store=Dummy(),
        ari_client=Dummy(),
        conversation_coordinator=None,
        fallback_playback_manager=None,
        streaming_config={
            'normalizer': {
                'enabled': True,
                'target_rms': 1400,
                'max_gain_db': 12.0,
            }
        },
        audio_transport="audiosocket",
    )


def test_apply_normalizer_increases_rms_within_bound():
    mgr = _make_manager()
    # Low amplitude sine ~ RMS ~= amp/sqrt(2)
    pcm = _make_sine_pcm16(amplitude=300, samples=800)  # 100ms at 8k
    before = _rms_pcm16(pcm)
    out = mgr._apply_normalizer(pcm, target_rms=1400, max_gain_db=12.0)
    after = _rms_pcm16(out)

    # Expect increase, but capped by 12 dB (~4x)
    assert after > before
    assert after <= before * (10 ** (12.0 / 20.0)) + 1.0


def test_apply_normalizer_target_zero_no_change():
    mgr = _make_manager()
    pcm = _make_sine_pcm16(amplitude=800, samples=800)
    out = mgr._apply_normalizer(pcm, target_rms=0, max_gain_db=12.0)
    assert out == pcm


def test_apply_normalizer_respects_small_max_gain():
    mgr = _make_manager()
    pcm = _make_sine_pcm16(amplitude=1000, samples=800)
    before = _rms_pcm16(pcm)
    out = mgr._apply_normalizer(pcm, target_rms=1200, max_gain_db=0.5)  # ~+0.5 dB max
    after = _rms_pcm16(out)

    # Expected RMS roughly before * 10^(0.5/20) with some integer rounding tolerance
    expected = before * (10 ** (0.5 / 20.0))
    assert after == pytest.approx(expected, rel=0.05)
