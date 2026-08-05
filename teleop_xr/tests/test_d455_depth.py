import unittest

import numpy as np

from teleop_xr.d455_depth import D455StereoVideoSource


class D455StereoSynthesisTest(unittest.TestCase):
    def test_constant_depth_keeps_left_and_projects_physical_right_view(self) -> None:
        color = np.zeros((9, 31, 3), dtype=np.uint8)
        color[:, 15, 0] = 255
        depth = np.ones((9, 31), dtype=np.float32)

        stereo = D455StereoVideoSource.synthesize_physical_stereo_sbs(
            color,
            depth,
            fx=100.0,
            baseline_m=0.06,
            near_m=0.25,
            far_m=4.0,
        )

        self.assertEqual(stereo.shape, (9, 62, 3))
        left_peak = int(np.argmax(stereo[4, :31, 0]))
        right_peak = int(np.argmax(stereo[4, 31:, 0]))
        self.assertEqual(left_peak, 15)
        self.assertLess(right_peak, 15)
        self.assertGreaterEqual(left_peak - right_peak, 4)

    def test_invalid_depth_keeps_left_texture_and_blacks_unknown_right(self) -> None:
        color = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
        depth = np.zeros((6, 8), dtype=np.float32)

        stereo = D455StereoVideoSource.synthesize_physical_stereo_sbs(
            color,
            depth,
            fx=100.0,
            baseline_m=0.095,
            near_m=0.25,
            far_m=4.0,
        )
        np.testing.assert_array_equal(stereo[:, :8], color)
        np.testing.assert_array_equal(stereo[:, 8:], np.zeros_like(color))

    def test_rgb_texture_coordinates_color_the_left_ir_view(self) -> None:
        color = np.zeros((4, 4, 3), dtype=np.uint8)
        color[:, 2, 1] = 255
        depth = np.ones((4, 4), dtype=np.float32)
        uv = np.zeros((4, 4, 2), dtype=np.float32)
        uv[..., 0] = 0.5
        uv[..., 1] = np.arange(4, dtype=np.float32)[:, None] / 4.0

        textured = D455StereoVideoSource.texture_left_infrared_view(
            color, uv, depth, near_m=0.25, far_m=4.0
        )

        np.testing.assert_array_equal(textured[..., 1], np.full((4, 4), 255))

    def test_ir_colorization_preserves_rgb_chroma_and_adds_stereo_detail(self) -> None:
        texture = np.zeros((9, 9, 3), dtype=np.uint8)
        texture[..., 2] = 180
        infrared = np.full((9, 9), 100, dtype=np.uint8)
        infrared[4, 4] = 220

        colored = D455StereoVideoSource.colorize_infrared_detail(
            infrared, texture
        )

        self.assertGreater(int(colored[4, 4, 2]), int(colored[0, 0, 2]))
        self.assertGreater(float(colored[..., 2].mean()), float(colored[..., 1].mean()))

    def test_color_and_depth_dimensions_must_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "dimensions"):
            D455StereoVideoSource.synthesize_physical_stereo_sbs(
                np.zeros((2, 2, 3), dtype=np.uint8),
                np.zeros((2, 3), dtype=np.float32),
                fx=100.0,
                baseline_m=0.095,
                near_m=0.25,
                far_m=4.0,
            )


if __name__ == "__main__":
    unittest.main()
