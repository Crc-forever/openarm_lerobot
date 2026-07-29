import unittest

from teleop_xr import _frontend_cache_control


class FrontendCacheControlTest(unittest.TestCase):
    def test_hashed_next_assets_are_immutable(self):
        self.assertEqual(
            _frontend_cache_control("/_next/static/chunks/example.js"),
            "public, max-age=31536000, immutable",
        )

    def test_entrypoint_and_runtime_config_are_not_cached(self):
        self.assertEqual(_frontend_cache_control("/"), "no-store, max-age=0")
        self.assertEqual(
            _frontend_cache_control("/ui/camera.json"),
            "no-store, max-age=0",
        )

    def test_robot_assets_can_revalidate_instead_of_redownloading(self):
        self.assertEqual(
            _frontend_cache_control("/robot_assets/openarm/link.stl"),
            "no-cache",
        )


if __name__ == "__main__":
    unittest.main()
