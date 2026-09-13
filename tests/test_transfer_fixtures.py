#!/usr/bin/env python3
"""Static integrity and telephony framing checks for Phase 0 transfer audio."""

import hashlib
from pathlib import Path
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
OVERLAY_FIXTURES = ROOT / ".fleet-overlay" / "fixtures"
FIXTURES = OVERLAY_FIXTURES if OVERLAY_FIXTURES.is_dir() else ROOT / "fixtures"
FRAME_BYTES = 320


class TransferFixtureTest(unittest.TestCase):
    def test_wav_integrity_and_metadata(self):
        path = FIXTURES / "phase0-transfer-8k-mono.wav"
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.stat().st_size, 275726)
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "e17ea78da84f3ab18a17dee4118ba3a9942df22fe69b465dd282014bb8393498",
        )

        with wave.open(str(path), "rb") as stream:
            self.assertEqual(stream.getnchannels(), 1)
            self.assertEqual(stream.getsampwidth(), 2)
            self.assertEqual(stream.getframerate(), 8000)
            self.assertEqual(stream.getcomptype(), "NONE")
            self.assertEqual(stream.getnframes(), 137841)

    def test_sln_integrity_and_cadence_shape(self):
        path = FIXTURES / "phase0-transfer-8k-mono.sln"
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        payload = path.read_bytes()
        self.assertEqual(len(payload), 275682)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "d82645c2926a8800d7bc41f6f127d19a8744da16df4b93371e7b46d0597f6057",
        )
        self.assertEqual(len(payload) % 2, 0)
        full_frames, tail_bytes = divmod(len(payload), FRAME_BYTES)
        self.assertEqual((full_frames, tail_bytes), (861, 162))
        self.assertEqual(tail_bytes % 2, 0)
        with wave.open(str(FIXTURES / "phase0-transfer-8k-mono.wav"), "rb") as stream:
            self.assertEqual(stream.readframes(stream.getnframes()), payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
