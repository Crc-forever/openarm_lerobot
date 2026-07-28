# PICO Wi-Fi 无线连接说明

# 查看电脑IP
hostname -I

# 初始化CAN：
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up

sudo ip link set can1 down
sudo ip link set can1 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can1 txqueuelen 1000
sudo ip link set can1 up


# 启动局域网遥操作
./scripts/start.sh robot --host 0.0.0.0 --port 4443

# PICO打开
https://192.168.43.84:4443