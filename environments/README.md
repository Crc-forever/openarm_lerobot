# Runtime environments

The project uses two isolated Python environments. They communicate through a
versioned runtime protocol; neither environment imports the other one.

## `control`

Owns LeRobot, OpenArm hardware access, cameras, recording, replay, and policy
execution. It is the only process allowed to open `can0` or `can1`.

## `teleop_ik`

The first phase owns only TeleopXR, Pico/WebXR input, and raw pose inspection.
It never opens a CAN interface or enables a motor. PyRoki/JAX and IK are added
only after the raw Pico input path passes its offline checks.

Each directory is an independent locked Python project. Runtime environments
live under Miniforge, outside the Git checkout. Do not create `.venv` inside
this repository.

Current phase (TeleopXR only):

```bash
conda activate openarm-teleop
python -m teleop_xr.demo --mode teleop --no-tui
```

This phase intentionally excludes PyTorch, CUDA, JAX, PyRoki, LeRobot, and all
hardware control dependencies. The demo only serves the WebXR UI and prints XR
input; it cannot command the arms.

The control and training environments remain deferred. Their CUDA/PyTorch
versions will be selected together with the later NVIDIA driver update.
