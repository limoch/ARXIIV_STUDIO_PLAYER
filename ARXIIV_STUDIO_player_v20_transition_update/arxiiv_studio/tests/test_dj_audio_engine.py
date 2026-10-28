import unittest
import numpy as np

from dj_audio_engine import crossfade_gains, mix_stereo_blocks, iter_pcm_blocks


class CrossfadeTests(unittest.TestCase):
    def test_linear_endpoints(self):
        self.assertEqual(crossfade_gains(0, "linear"), (1.0, 0.0))
        self.assertEqual(crossfade_gains(1, "linear"), (0.0, 1.0))

    def test_equal_power_midpoint(self):
        a, b = crossfade_gains(0.5, "equal_power")
        self.assertAlmostEqual(a, 2 ** -0.5, places=6)
        self.assertAlmostEqual(b, 2 ** -0.5, places=6)
        self.assertAlmostEqual(a*a + b*b, 1.0, places=6)

    def test_crossfader_routes_decks(self):
        a = np.full((128, 2), 0.25, dtype=np.float32)
        b = np.full((128, 2), 0.50, dtype=np.float32)
        left = mix_stereo_blocks(a, b, crossfader=-1, curve="linear", limiter=False)
        right = mix_stereo_blocks(a, b, crossfader=1, curve="linear", limiter=False)
        np.testing.assert_allclose(left, a)
        np.testing.assert_allclose(right, b)

    def test_gains_are_applied(self):
        a = np.ones((64, 2), dtype=np.float32) * 0.2
        out = mix_stereo_blocks(a, None, gain_a=0.5, master_gain=0.5,
                                crossfader=-1, curve="linear", limiter=False)
        np.testing.assert_allclose(out, 0.05, atol=1e-7)

    def test_limiter_bounds_output(self):
        a = np.full((32, 2), 4.0, dtype=np.float32)
        out = mix_stereo_blocks(a, a, crossfader=0, curve="linear", limiter=True)
        self.assertTrue(np.isfinite(out).all())
        self.assertLessEqual(float(np.max(np.abs(out))), 1.0)

    def test_blocks_cover_all_frames(self):
        x = np.zeros((2500, 2), dtype=np.float32)
        blocks = list(iter_pcm_blocks(x, 1024))
        self.assertEqual([len(b) for b in blocks], [1024, 1024, 452])
        self.assertEqual(sum(map(len, blocks)), len(x))


if __name__ == "__main__":
    unittest.main()
