"""R760 SSH 工具 — 远程执行命令、上传文件。

Usage:
    python scripts/r760-ssh.py exec "ls -la"
    python scripts/r760-ssh.py upload saas/ /root/manon/saas/
    python scripts/r760-ssh.py deploy          # 上传全部代码 + 重启服务
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import paramiko

HOST = "117.131.45.179"
PORT = 2212
USER = "root"
PASSWORD = "Ubuntu@2026!@#"


def _connect() -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=10)
    return ssh


def _exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> str:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    return out


def _upload_dir(sftp, local_dir: str, remote_dir: str) -> int:
    count = 0
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".pyc"):
                continue
            local_path = os.path.join(root, f)
            rel = os.path.relpath(local_path, local_dir).replace("\\", "/")
            remote_path = f"{remote_dir}/{rel}"
            # ensure remote dir exists
            remote_parent = os.path.dirname(remote_path)
            try:
                sftp.stat(remote_parent)
            except FileNotFoundError:
                _mkdir_p(sftp, remote_parent)
            sftp.put(local_path, remote_path)
            print(f"  {remote_path}")
            count += 1
    return count


def _mkdir_p(sftp, path: str):
    parts = path.split("/")
    current = ""
    for part in parts:
        current += f"/{part}" if current else part
        if not current:
            current = "/"
            continue
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def cmd_exec(args):
    ssh = _connect()
    _exec(ssh, args.cmd)
    ssh.close()


def cmd_upload(args):
    ssh = _connect()
    sftp = ssh.open_sftp()
    count = _upload_dir(sftp, args.local, args.remote)
    print(f"\nUploaded {count} files")
    sftp.close()
    ssh.close()


def cmd_deploy(args):
    """Upload saas/ + matrixone_graph/ and restart the service."""
    project = str(Path(__file__).resolve().parents[1])
    ssh = _connect()
    sftp = ssh.open_sftp()

    remote = "/root/manon"
    for d in ["saas", "saas/routers", "saas/services", "saas/cli", "saas/static", "matrixone_graph"]:
        try:
            sftp.mkdir(f"{remote}/{d}")
        except IOError:
            pass

    total = 0
    for sub in ["saas", "matrixone_graph"]:
        total += _upload_dir(sftp, os.path.join(project, sub), f"{remote}/{sub}")

    # upload requirements.txt
    req = os.path.join(project, "requirements.txt")
    if os.path.exists(req):
        sftp.put(req, f"{remote}/requirements.txt")
        total += 1

    sftp.close()
    print(f"\nUploaded {total} files. Restarting service...")

    # kill old process and restart
    _exec(ssh, "pkill -f 'python3 -m saas' || true")
    _exec(ssh, f"cd {remote} && nohup python3 -m saas > /root/saas.log 2>&1 &")
    import time; time.sleep(2)
    _exec(ssh, "cat /root/saas.log | tail -5")
    ssh.close()


def main():
    p = argparse.ArgumentParser(prog="r760-ssh", description="R760 SSH 工具")
    sub = p.add_subparsers(dest="subcmd")

    ep = sub.add_parser("exec", help="Execute remote command")
    ep.add_argument("cmd")

    up = sub.add_parser("upload", help="Upload directory")
    up.add_argument("local")
    up.add_argument("remote")

    sub.add_parser("deploy", help="Deploy code + restart service")

    args = p.parse_args()
    if not args.subcmd:
        p.print_help()
        sys.exit(1)

    {"exec": cmd_exec, "upload": cmd_upload, "deploy": cmd_deploy}[args.subcmd](args)


if __name__ == "__main__":
    main()
