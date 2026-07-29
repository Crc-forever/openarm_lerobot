# PICO 4 / PICO 4 Ultra USB 遥操作（含投屏）

## 1. 连接机械臂

机械臂上电并插好两路 USB-CAN 后，在仓库根目录粘贴以下 8 行：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up
sudo ip link set can1 down
sudo ip link set can1 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can1 txqueuelen 1000
sudo ip link set can1 up
```

## 2. 连接 Pico

PICO 开启开发者模式和 USB 调试，用 USB 数据线连接电脑，并在头显中允许
这台电脑调试。

终端一（遥操作）：

```bash
./scripts/start_pico_usb.sh
```

终端二（投屏，可选）：

```bash
./scripts/start_pico_cast.sh
```

PICO Browser 打开 `https://localhost:4443`，允许证书继续访问，点击
`MR 透视遥操作`。启动脚本会显示检测到的头显型号、PICO OS 和浏览器版本；
PICO 4 Ultra 应显示为 `PICO 4 Ultra`。

## 3. 操作

- 同时按住左右侧握键：从当前位置接管双臂；
- 松开任意侧握键：停止跟随并保持；
- 左右食指扳机：控制对应夹爪；
- 左手柄 `Y` + 右手柄 `B` 按住约 1.5 秒：重置操作朝向；
- 左手射线指向深度相机窗口并按住 `Y`：拖动窗口，窗口会自动朝向用户；
- 结束时先松开侧握键，再回到遥操作终端按 `Ctrl+C`。

首次运行请低速、小幅移动，并确保随时可以物理断电。
