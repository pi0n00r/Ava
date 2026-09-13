from src.providers.google_live import _merge_transcription_fragment


def test_merge_transcription_fragment_dedupes_exact_repeats():
    buf, last = _merge_transcription_fragment("", "Goodbye!", "")
    assert buf == "Goodbye!"
    buf, last = _merge_transcription_fragment(buf, "Goodbye!", last)
    assert buf == "Goodbye!"


def test_merge_transcription_fragment_replaces_cumulative_text():
    buf, last = _merge_transcription_fragment("Thank you", "Thank you for calling", "Thank you")
    assert buf == "Thank you for calling"
    assert last == "Thank you for calling"


def test_merge_transcription_fragment_ignores_suffix_duplicates():
    buf, last = _merge_transcription_fragment("Hello", "lo", "")
    assert buf == "Hello"
    assert last == "lo"

