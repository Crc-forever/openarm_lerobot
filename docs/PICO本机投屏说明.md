# Pico USB 本机投屏

Pico 通过 USB 和 ADB 连接后，执行：

```bash
cd /mnt/data/清敏感知/Openarm/Openarm_lerobot
./scripts/start_pico_cast.sh
```

电脑会打开 `Pico 4 USB 投屏` 窗口。

投屏使用项目内固定版本的 scrcpy，不需要安装系统软件包。工具首次缺失时，
脚本会下载官方 Linux 静态包到：

```text
Openarm_lerobot/.tools/
```

`.tools/` 已加入 `.gitignore`，不会提交到 Git，也不属于机器人部署运行环境。

默认设置：

- USB 传输；
- 只裁剪展示右眼视野；
- 默认全屏铺满电脑显示器，按 `Ctrl+F` 切换全屏；
- 右眼原始区域为 `2160×2160`，最大投屏尺寸为 1920；
- 最大 60 FPS；
- 16 Mbps；
- 不传音频；
- 只投屏，不允许电脑鼠标控制 Pico，避免误操作。

停止投屏时关闭窗口或在终端按 `Ctrl+C`。

如果需要临时改变清晰度，可以追加 scrcpy 参数。例如：

```bash
./scripts/start_pico_cast.sh --max-size 1280 --video-bit-rate 8M
```
