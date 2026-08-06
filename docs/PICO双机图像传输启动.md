# PICO 双机图像传输启动

当前稳定链路：

```text
PICO 4 Ultra 原生左右彩色相机
  -> 2560x960 SBS H.264
  -> 电脑 8091/TCP 中继（不转码）
  -> PICO 4 的 4443 网页
  -> WebCodecs + CanvasTexture 双目渲染
```

## 每次启动

1. 电脑、PICO 4 Ultra、PICO 4 连接同一个 5 GHz 局域网。
2. 电脑启动服务：

   ```bash
   cd /项目路径/Openarm_lerobot
   ./scripts/start.sh teleop --no-tui
   ```

3. Ultra 打开“资源库 -> 未知来源 -> OpenArm Ultra Stereo Sender”。
4. 如果系统询问摄像头权限，选择允许。
5. PICO 4 浏览器打开：

   ```text
   https://电脑局域网IP:4443/
   ```

6. 页面显示“Ultra 已连接”后，点击“进入双目 VR”。

## 当前固定地址

Ultra Sender 当前发送到：

```text
192.168.50.86:8091
```

如果电脑 IP 改变，需要修改
`pico_camera_sender/native_sender/src/main/cpp/StereoStreamer.cpp` 中的 `kHost`，
重新构建并安装 Sender APK。PICO 4 网页地址也要改成新的电脑 IP。

## 检查状态

```bash
curl -k https://127.0.0.1:4443/api/pico-ultra-stereo/status
```

正常应看到：

```json
{"sender_connected": true, "received_access_units": 100}
```

`received_access_units` 持续增长表示视频正在到达电脑。

## 注意

- 只使用 `OpenArm Ultra Stereo Sender`。
- 不使用已删除的 MediaProjection 屏幕采集版本。
- Ultra 电量过低时系统会停止相机出帧；使用前先充足电。
- 电脑中继只保留最新 H.264 帧，不进行重新编码。
