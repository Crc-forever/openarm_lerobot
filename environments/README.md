# Runtime environments

The project uses two isolated Python environments. They communicate through a
versioned runtime protocol; neither environment imports the other one.

## `control`

Owns LeRobot, OpenArm hardware access, cameras, recording, replay, and policy
execution. It is the only process allowed to open `can0` or `can1`.

## `teleop_ik`

Owns TeleopXR, Pico/WebXR input, pose processing, and PyRoki IK. It never opens
a CAN interface or enables a motor.

Each directory is an independent uv project with its own lock file and virtual
environment:

```bash
cd environments/control
uv sync

cd ../teleop_ik
uv sync
```

The phase-one control environment deliberately retains the CUDA/PyTorch build
supported by the currently installed NVIDIA driver. Training dependencies and
the GPU driver will be upgraded together in a later, separately tested change.
