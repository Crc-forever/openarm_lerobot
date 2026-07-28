import os
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        dist_dir = os.path.join(self.root, "teleop_xr", "dist")
        if not os.path.isfile(os.path.join(dist_dir, "index.html")):
            raise RuntimeError("Committed Pico WebXR assets are missing")
        if "teleop_xr/dist" not in build_data["artifacts"]:
            build_data["artifacts"].append("teleop_xr/dist")
