# Pico USB 有线连接说明

推荐使用：

```text
Pico 浏览器 -> USB/ADB -> TeleopXR -> PyRoki IK -> LeRobot action（离线）
```

该模式不依赖 Wi-Fi，且不会连接 `can0/can1`。

## 1. 电脑安装 ADB

本机当前尚未安装 ADB。执行：

```bash
sudo apt update
sudo apt install -y adb android-sdk-platform-tools-common
```

第二个软件包提供 Ubuntu 的 Android USB 设备规则，避免每次使用 ADB
都需要 root 权限。

安装后检查：

```bash
adb version
```

## 2. Pico 开启 USB 调试

在 Pico 中操作：

1. 打开“设置”；
2. 进入“通用/常规”；
3. 找到“软件版本”，连续点击，直到出现“开发者”选项；
4. 进入“开发者”；
5. 打开“USB 调试”。

不同 Pico 系统版本的中文菜单名称可能略有差异。

## 3. 用 USB 数据线连接

使用支持数据传输的 USB 3.x 数据线连接 Pico 和电脑。

戴上 Pico 后会出现 USB 调试授权提示：

1. 勾选“始终允许此电脑”；
2. 点击“允许”。

电脑执行：

```bash
adb devices -l
```

正确结果中应看到一台状态为 `device` 的设备：

```text
设备序列号  device  ...
```

如果显示 `unauthorized`，需要重新戴上 Pico 完成授权。

## 4. 一条命令启动有线离线遥操作

```bash
cd /mnt/data/清敏感知/Openarm/Openarm_lerobot
./scripts/start_pico_usb.sh
```

脚本会自动执行 USB 端口反向转发：

```text
Pico localhost:4443 -> USB -> 电脑 localhost:4443
```

看到 `Server started` 后，在 Pico 系统浏览器打开：

```text
https://localhost:4443
```

首次打开：

1. 在证书警告页选择“高级/继续访问”；
2. 页面加载后点击 `VR Mode`；
3. 允许 VR 和动作追踪权限；
4. 确认页面显示 `Connected`。

项目本地证书包含 `localhost` 和 `127.0.0.1`，有效期至 2036 年。

## 5. 手柄操作

- 同时按住左右侧握键：使能虚拟双臂 IK；
- 松开任意侧握键：停止更新目标；
- 左右食指扳机：控制对应夹爪。

当前只观察虚拟双臂，不连接机械臂主板。

## 6. 停止

电脑终端按：

```text
Ctrl+C
```

脚本退出时会自动删除 USB 端口转发。

## 7. 常见问题

### `adb: command not found`

回到第 1 步安装 ADB。

### `no permissions`

先重新插拔 USB；若仍失败，确认已经安装：

```bash
sudo apt install -y android-sdk-platform-tools-common
```

然后退出登录并重新登录 Ubuntu。

### `unauthorized`

在 Pico 的 USB 调试授权弹窗中选择允许。如果弹窗不再出现：

1. Pico 开发者设置中撤销 USB 调试授权；
2. 重新插拔数据线；
3. 再次允许这台电脑。

### 没有识别到设备

- 确认数据线支持数据传输，不是仅充电线；
- 更换电脑 USB 3.x 接口；
- 保持 Pico 开机并解锁；
- 确认 USB 调试已开启。

### Pico 打不开 `https://localhost:4443`

检查转发：

```bash
adb reverse --list
```

应包含：

```text
tcp:4443 tcp:4443
```

同时确认电脑终端已经显示 `Server started`。

