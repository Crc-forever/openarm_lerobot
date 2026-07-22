# OpenArm LeRobot

OpenArm 双臂 VR 遥操、数据采集与策略部署的新主线工程。

## 目标

- Pico VR 通过 TeleopXR 提供左右手位姿和按键输入。
- PyRoki 使用 OpenArm 模型求解双臂逆运动学。
- LeRobot 统一负责机械臂驱动、状态、相机、数据集、训练和推理。
- VR 遥操与模型推理共用同一套动作格式和安全控制入口。

## 工程边界

- `Openarm_lerobot/` 是今后的新主线。
- `../openarm_ws/` 是旧工程，只用于迁移已验证的标定、CAN 配置、IK 映射和安全参数。
- 新主线不直接修改或删除旧工程文件。

## 目标数据流

```text
Pico VR
  -> TeleopXR
  -> 位姿处理
  -> PyRoki IK
  -> 统一动作与安全层
  -> LeRobot OpenArm 驱动
  -> SocketCAN (can0 / can1)
  -> OpenArm 双臂
```

```text
相机 + 机械臂实际状态 + 最终下发动作
  -> LeRobot Dataset
  -> 策略训练
  -> 策略推理
  -> 同一安全控制入口
```

## 当前状态

工程正在搭建中。依赖版本、目录模块和真机接口将在后续步骤中逐项确定。
