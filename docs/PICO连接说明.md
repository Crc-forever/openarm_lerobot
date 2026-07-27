# PICO Wi-Fi 无线连接说明

# 查看电脑IP
hostname -I

# 初始化CAN：
sudo ./scripts/setup_can.sh

# 启动局域网遥操作
./scripts/start.sh robot -host 0.0.0.0 --port 4443

# PICO打开
https://192.168.43.84:4443