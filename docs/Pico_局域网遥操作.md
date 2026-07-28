# Pico 局域网遥操作

电脑与 Pico 连接同一个局域网。

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

## 2. 启动

```bash
./scripts/start.sh robot --host 0.0.0.0 --port 4443
```

终端会显示 Pico 应打开的地址。若未显示，用 `hostname -I` 查看电脑的局域网
IP，然后在 Pico 浏览器打开：

```text
https://电脑IP:4443
```

允许证书继续访问，点击 `VR Mode`。

## 3. 操作

- 同时按住左右侧握键：从当前位置接管双臂；
- 松开任意侧握键：停止跟随并保持；
- 左右食指扳机：控制对应夹爪；
- 左手柄 `Y` + 右手柄 `B` 按住约 1.5 秒：重置操作朝向；
- 结束时先松开侧握键，再回到终端按 `Ctrl+C`。

首次运行请低速、小幅移动，并确保随时可以物理断电。
