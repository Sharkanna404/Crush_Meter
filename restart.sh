#!/bin/bash
# 心动解码器重启脚本
# 用法: ./restart.sh

cd "$(dirname "$0")"

# 杀掉旧进程
OLD_PID=$(pgrep -f "python3 main.py" | head -1)
if [ -n "$OLD_PID" ]; then
    echo "正在停止旧服务器 (PID: $OLD_PID)..."
    kill -9 "$OLD_PID" 2>/dev/null
    sleep 1
fi

# 启动新服务器
echo "正在启动心动解码器..."
nohup python3 main.py > /tmp/heart.log 2>&1 &
NEW_PID=$!

# 等待启动
sleep 2

# 验证端口
if ss -tlnp | grep -q ":8080"; then
    echo "重启成功! PID: $NEW_PID"
    echo "访问: http://111.229.9.79:8080/"
    echo "日志: tail -f /tmp/heart.log"
else
    echo "启动可能失败，请检查日志: tail -f /tmp/heart.log"
    exit 1
fi
