# OpenArm LeRobot

Pico VR 控制双臂 OpenArm，并用 LeRobot 同步采集双臂、夹爪和四路相机数据。

## 系统环境

- 推荐 Ubuntu 22.04 x86_64 + ROS 2 Humble（Aurora930 配套组合）；
- 同时兼容 Ubuntu 24.04 x86_64 + ROS 2 Jazzy；
- NVIDIA 显卡及可用驱动；
- 两路 SocketCAN：左臂 `can0`，右臂 `can1`；
- Python 3.12。

新机 clone 后先安装系统依赖：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv git curl openssl adb android-sdk-platform-tools-common \
  can-utils ffmpeg libgl1 libglib2.0-0
```

然后在仓库根目录执行：

```bash
./scripts/install.sh
```

脚本会创建仓库内的 `.venv`，安装固定版本的 LeRobot、JAX、PyRoki 和
TeleopXR，并为本机生成 HTTPS 证书。以后不需要激活虚拟环境，直接运行
`scripts/` 中的入口即可。

已有机器如果保留了原来的 Conda `lerobot` 环境，启动脚本会自动兼容：
优先使用 `.venv`，不存在时使用 Conda 环境。

部署后可先做软件侧预检：

```bash
./scripts/preflight.sh --software-only
```

## 操作文档

1. [Pico USB 遥操作（含投屏）](docs/Pico_USB遥操作.md)
2. [Pico 局域网遥操作](docs/Pico_局域网遥操作.md)
3. [数据采集](docs/数据采集.md)
4. [PICO 双机图像传输启动](docs/PICO双机图像传输启动.md)

## 目录

```text
configs/             硬件、遥操作与安全参数
docs/                三份操作说明
openarm_description/ OpenArm v1 双臂模型
pyroki/              IK 求解器
scripts/             安装、遥操作和采集入口
teleop_xr/           Pico WebXR、OpenArm IK、控制与记录
```

`data/`、`logs/`、`.venv/` 和 `.vendor/` 均不会提交到 Git。

## 数据采集额外环境

遥操作不需要 ROS。只有四相机数据采集需要 ROS 2 和 Aurora930 厂商驱动：
Ubuntu 22.04 使用 Humble，Ubuntu 24.04 使用 Jazzy。先从相机随附资料取得：

```text
deptrum-ros-driver-aurora930-x86_64-0.2.10-source.tar.gz
```

然后执行：

```bash
./scripts/install_aurora930.sh /path/to/deptrum-ros-driver-aurora930-x86_64-0.2.10-source.tar.gz
```

脚本会按 Ubuntu 版本选择 ROS 2、编译驱动并配置 Aurora930 udev 权限。
在 22.04 上，相机订阅会通过系统 Python 3.10 桥接到 LeRobot 的 Python
3.12 进程，避免 Humble `rclpy` 的 ABI 冲突。厂商驱动包不是公开 Git
依赖，且超过 GitHub 单文件限制，因此不放入仓库。

机械臂和相机全部接好后执行：

```bash
./scripts/setup_can.sh
./scripts/preflight.sh
```
