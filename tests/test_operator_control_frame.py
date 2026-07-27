from types import SimpleNamespace
import unittest

import jax.numpy as jnp
import jaxlie
import numpy as np

from teleop_xr.demo.__main__ import IKWorker
from teleop_xr.ik.controller import IKController
from teleop_xr.messages import XRState


def _button(pressed: bool = False) -> dict[str, object]:
    return {"pressed": pressed, "touched": pressed, "value": float(pressed)}


def _controller(handedness: str, *, secondary: bool, squeeze: bool = False):
    buttons = [_button() for _ in range(6)]
    buttons[1] = _button(squeeze)
    buttons[5] = _button(secondary)
    return {
        "role": "controller",
        "handedness": handedness,
        "gripPose": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
        },
        "gamepad": {"buttons": buttons, "axes": []},
    }


class OperatorControlFrameTests(unittest.TestCase):
    def _controller(self) -> IKController:
        robot = SimpleNamespace(
            ros_to_base=jaxlie.SO3.identity(),
            base_to_ros=jaxlie.SO3.identity(),
            supported_frames={"left", "right"},
        )
        return IKController(robot, solver=None)

    @staticmethod
    def _head_state(yaw_rad: float) -> XRState:
        rotation = jaxlie.SO3.from_rpy_radians(0.0, 0.0, yaw_rad)
        w, x, y, z = np.asarray(rotation.wxyz)
        return XRState.model_validate(
            {
                "timestamp_unix_ms": 1.0,
                "devices": [
                    {
                        "role": "head",
                        "handedness": "none",
                        "pose": {
                            "position": {"x": 0.0, "y": 0.0, "z": 1.7},
                            "orientation": {
                                "w": float(w),
                                "x": float(x),
                                "y": float(y),
                                "z": float(z),
                            },
                        },
                    }
                ],
            }
        )

    def test_head_heading_rotates_ros_world_motion_into_operator_forward(self):
        controller = self._controller()
        controller.reset_operator_frame(self._head_state(np.pi / 2))

        initial = jaxlie.SE3.identity()
        # Incoming poses are already ROS FLU. Facing ROS +Y means a physical
        # forward hand motion is world +Y and must map back to robot +X.
        current = jaxlie.SE3.from_rotation_and_translation(
            jaxlie.SO3.identity(),
            jnp.array([0.0, 0.1, 0.0]),
        )
        target = controller.compute_teleop_transform(
            current,
            initial,
            jaxlie.SE3.identity(),
        )

        np.testing.assert_allclose(
            np.asarray(target.translation()),
            np.array([0.1, 0.0, 0.0]),
            atol=1e-6,
        )

    def test_reset_changes_translation_axes_not_only_visual_heading(self):
        controller = self._controller()
        initial = jaxlie.SE3.identity()
        world_left_motion = jaxlie.SE3.from_rotation_and_translation(
            jaxlie.SO3.identity(),
            jnp.array([0.0, 0.1, 0.0]),
        )

        controller.reset_operator_frame(self._head_state(0.0))
        before_reset = controller.compute_teleop_transform(
            world_left_motion,
            initial,
            jaxlie.SE3.identity(),
        )
        controller.reset_operator_frame(self._head_state(np.pi / 2))
        after_reset = controller.compute_teleop_transform(
            world_left_motion,
            initial,
            jaxlie.SE3.identity(),
        )

        np.testing.assert_allclose(
            np.asarray(before_reset.translation()),
            np.array([0.0, 0.1, 0.0]),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            np.asarray(after_reset.translation()),
            np.array([0.1, 0.0, 0.0]),
            atol=1e-6,
        )

    def test_reset_rotates_controller_orientation_delta_into_new_axes(self):
        controller = self._controller()
        controller.reset_operator_frame(self._head_state(np.pi / 2))

        initial = jaxlie.SE3.identity()
        # A world-frame +Y rotation is forward-axis roll when facing +Y.
        current = jaxlie.SE3.from_rotation_and_translation(
            jaxlie.SO3.from_rpy_radians(0.0, np.deg2rad(15.0), 0.0),
            jnp.zeros(3),
        )
        target = controller.compute_teleop_transform(
            current,
            initial,
            jaxlie.SE3.identity(),
        )

        expected = jaxlie.SO3.from_rpy_radians(np.deg2rad(15.0), 0.0, 0.0)
        np.testing.assert_allclose(
            np.asarray(target.rotation().as_matrix()),
            np.asarray(expected.as_matrix()),
            atol=1e-6,
        )

    def test_ros_head_heading_ignores_pitch_and_roll(self):
        controller = self._controller()
        state = XRState.model_validate(
            {
                "timestamp_unix_ms": 1.0,
                "devices": [
                    {
                        "role": "head",
                        "handedness": "none",
                        "pose": {
                            "position": {"x": 0.0, "y": 0.0, "z": 1.7},
                            "orientation": {
                                "w": 0.6830127,
                                "x": 0.1830127,
                                "y": 0.1830127,
                                "z": 0.6830127,
                            },
                        },
                    }
                ],
            }
        )
        controller.reset_operator_frame(state)

        initial = jaxlie.SE3.identity()
        # This pose faces ROS +Y while also carrying roll/pitch components.
        current = jaxlie.SE3.from_rotation_and_translation(
            jaxlie.SO3.identity(),
            jnp.array([0.0, 0.1, 0.0]),
        )
        target = controller.compute_teleop_transform(
            current,
            initial,
            jaxlie.SE3.identity(),
        )

        np.testing.assert_allclose(
            np.asarray(target.translation()),
            np.array([0.1, 0.0, 0.0]),
            atol=1e-6,
        )

    def test_y_and_b_require_released_grips_and_one_continuous_hold(self):
        worker = object.__new__(IKWorker)
        worker._coordinate_reset_started_at = None
        worker._coordinate_reset_fired = False
        state = XRState.model_validate(
            {
                "timestamp_unix_ms": 1.0,
                "devices": [
                    _controller("left", secondary=True),
                    _controller("right", secondary=True),
                ],
            }
        )

        self.assertFalse(worker._coordinate_reset_requested(state, 10.0))
        self.assertFalse(worker._coordinate_reset_requested(state, 11.49))
        self.assertTrue(worker._coordinate_reset_requested(state, 11.5))
        self.assertFalse(worker._coordinate_reset_requested(state, 12.0))

        squeezed = XRState.model_validate(
            {
                "timestamp_unix_ms": 2.0,
                "devices": [
                    _controller("left", secondary=True, squeeze=True),
                    _controller("right", secondary=True),
                ],
            }
        )
        self.assertFalse(worker._coordinate_reset_requested(squeezed, 13.0))
        self.assertIsNone(worker._coordinate_reset_started_at)


if __name__ == "__main__":
    unittest.main()
