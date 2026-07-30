from fractions import Fraction
import unittest

from teleop_xr.video_stream import WallClockVideoTimestamp


class WallClockVideoTimestampTest(unittest.TestCase):
    def test_tracks_actual_fifteen_fps_frame_cadence(self) -> None:
        timestamp = WallClockVideoTimestamp()

        first_pts, time_base = timestamp.next(10.0)
        second_pts, _ = timestamp.next(10.0 + 1.0 / 15.0)

        self.assertEqual(first_pts, 0)
        self.assertEqual(second_pts, 6000)
        self.assertEqual(time_base, Fraction(1, 90_000))

    def test_remains_strictly_monotonic_at_equal_clock_samples(self) -> None:
        timestamp = WallClockVideoTimestamp()

        first_pts, _ = timestamp.next(10.0)
        second_pts, _ = timestamp.next(10.0)

        self.assertEqual(first_pts, 0)
        self.assertEqual(second_pts, 1)


if __name__ == "__main__":
    unittest.main()
