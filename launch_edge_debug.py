"""
一键启动 Edge 并开启远程调试
用法：
    python launch_edge_debug.py

会自动查找 Edge 安装位置并以 --remote-debugging-port=9222 启动。
如果 Edge 已经在运行，会提示先关闭。
"""

import os
import subprocess
import sys
from pathlib import Path

PORT = 9222


def find_edge():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def main():
    edge = find_edge()
    if not edge:
        print("未找到 Edge 安装位置，请手动启动并加上 --remote-debugging-port=9222 参数")
        sys.exit(1)

    print(f"Edge 路径: {edge}")

    # 检查端口是否已被占用
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", PORT))
        sock.close()
    except OSError:
        print(f"\n端口 {PORT} 已被占用，Edge 可能已经在运行。")
        print(f"请打开 http://localhost:{PORT}/json 查看调试页面列表。")
        sys.exit(0)

    print(f"启动 Edge，远程调试端口 = {PORT}")
    print("在打开的 Edge 中登录平台并导航到课程页面，然后运行相关脚本")

    # 使用 --user-data-dir 独立配置目录，避免与已登录的 Edge 冲突
    user_data = Path(os.environ.get("TEMP", r"C:\Temp")) / "edge_auto_watch"
    user_data.mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
        [edge, f"--remote-debugging-port={PORT}", f"--user-data-dir={user_data}"],
        creationflags=0x00000008,  # DETACHED_PROCESS
    )
    print("Edge 已启动，几秒后可访问 http://localhost:9222/json 验证")


if __name__ == "__main__":
    main()