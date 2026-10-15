# -*- coding: utf-8 -*-
"""停止演示：结束 8181(phmlite) 与 4178(AGH serve) 的本机监听进程。"""
import subprocess


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                         text=True).stdout
    pids = {line.split()[-1] for line in out.splitlines()
            if f":{port}" in line and "LISTENING" in line}
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    return sorted(pids)


for port in (8181, 4178):
    killed = kill_port(port)
    print(f"端口 {port}: " + ("已结束进程 " + ",".join(killed) if killed else "无监听"))
print("演示已停止。")
