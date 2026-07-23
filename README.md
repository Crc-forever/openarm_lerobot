# OpenArm

双臂 OpenArm 的 Pico VR 遥操作、LeRobot 数据采集和训练工程。

## 架构

```text
Pico
  -> TeleopXR
  -> OpenArm 双臂 IK（PyRoki）
  -> LeRobot
  -> can0 / can1
  -> 左右机械臂
```

项目只使用一个 Python 环境。TeleopXR、PyRoki 和 OpenArm 模型源码随
项目保存；LeRobot 使用固定版本安装。

## 目录

```text
Openarm_lerobot/
├── teleop_xr/           # Pico 接收、手柄映射和双臂 IK 源码
├── pyroki/              # IK 求解器源码
├── openarm_description/ # OpenArm URDF、Xacro 和网格
├── configs/             # 硬件和遥操作配置
└── scripts/             # 安装和启动入口
```

## 安装

在新电脑上执行一次：

```bash
./scripts/install.sh
```

脚本创建名为 `lerobot` 的唯一 Conda 环境，安装固定版本的 LeRobot，
并从本项目目录安装 TeleopXR。源码修改后不需要重新复制。

当前默认不安装 PyRoki/JAX。需要启用 IK 时执行：

```bash
./scripts/install.sh --with-ik
```

该选项安装 CPU 版 JAX，不安装 CUDA，也不修改显卡驱动。

## 启动

Pico 连接方法：

- [Pico USB 有线连接说明](docs/PICO有线连接说明.md)（优先）；
- [Pico USB 本机投屏](docs/PICO本机投屏说明.md)；
- [Pico Wi-Fi 连接说明](docs/PICO连接说明.md)。

数据采集方法见：[LeRobot 数据采集说明](docs/数据采集说明.md)。

只启动 Pico/TeleopXR：

```bash
./scripts/start.sh teleop
```

IK 环境准备好以后：

```bash
./scripts/start.sh ik
```

此命令运行完整的 `Pico -> IK -> LeRobot action` 离线链路，不访问 CAN。

真机入口为：

```bash
./scripts/start.sh robot
```

`robot` 会连接 `can0/can1` 并使能电机。当前阶段不要执行；应先完成低速、
急停和方向核对。

## 当前状态

- TeleopXR源码、PyRoki源码和OpenArm模型已经迁入新项目；
- TeleopXR基础服务可以独立启动；
- CPU版IK依赖已经安装；
- IK输出已转换为LeRobot双臂OpenArm标准action；
- 离线完整链路已经启动验证；
- LeRobot数据采集已接入VR动作并通过模拟帧验证；
- 当前相机尚未配置；
- `teleop`和`ik`不会访问CAN或使能电机；
- 只有显式执行`start.sh robot`才会连接真机。

旧工程 `../openarm_ws/` 保留不动，仅用于核对已经验证的映射、滤波和硬件参数。
