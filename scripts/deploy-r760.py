"""Deploy saas + matrixone_graph to R760 server.

Usage:
  python scripts/deploy-r760.py              # 强制部署
  python scripts/deploy-r760.py --auto       # 仅服务端文件有变更时部署
  python scripts/deploy-r760.py --setup-systemd  # 首次安装 systemd 服务

Since R760 cannot reach GitHub, this script pushes code from local.
Only syncs the directories the server actually needs:
  saas/, matrixone_graph/, requirements.txt
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import time

import paramiko

HOST = "117.131.45.179"
PORT = 2212
USER = "root"
PASS = "Ubuntu@2026!@#"
REMOTE_DIR = "/root/manon"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only sync what the server needs to run `python3 -m saas`
SYNC_PATHS = ["saas", "matrixone_graph", "requirements.txt"]
DEPLOY_STAMP = os.path.join(ROOT, ".last-deploy-hash")

SYSTEMD_UNIT = """\
[Unit]
Description=Manon SaaS API
After=network.target

[Service]
WorkingDirectory=/root/manon
ExecStart=/usr/bin/python3 -m saas
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=SAAS_LLM_API_KEY=sk-cFnE5mi2zKEiWZa89TdFJcQQVKO4BTgSgz8E5EGOIpEpCpXJ

[Install]
WantedBy=multi-user.target
"""

def server_files_hash():
    """Hash server-relevant paths by content — works for untracked dirs too."""
    h = hashlib.sha256()
    for sp in SYNC_PATHS:
        full = os.path.join(ROOT, sp)
        if os.path.isfile(full):
            h.update(open(full, "rb").read())
        elif os.path.isdir(full):
            for dirpath, _, filenames in sorted(os.walk(full)):
                for fn in sorted(filenames):
                    if fn.endswith(".pyc") or "__pycache__" in dirpath:
                        continue
                    fp = os.path.join(dirpath, fn)
                    h.update(fp.encode())
                    h.update(open(fp, "rb").read())
    return h.hexdigest()


def needs_deploy():
    """Check if server-relevant files changed since last deploy."""
    current = server_files_hash()
    if not current:
        return True  # can't determine, deploy to be safe
    if not os.path.exists(DEPLOY_STAMP):
        return True  # never deployed
    last = open(DEPLOY_STAMP).read().strip()
    return current != last


def save_deploy_stamp():
    """Record current hash so --auto can skip unchanged deploys."""
    h = server_files_hash()
    if h:
        with open(DEPLOY_STAMP, "w") as f:
            f.write(h)


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15)
    return c


def run(c, cmd, timeout=30):
    """Run command on server, return stdout."""
    _, so, se = c.exec_command(cmd, timeout=timeout)
    out = so.read().decode().strip()
    err = se.read().decode().strip()
    if err:
        print(f"  STDERR: {err}")
    return out


def deploy(c):
    """Package only saas + matrixone_graph, upload, extract on server."""
    print("=== 打包代码 ===")
    tar_path = os.path.join(tempfile.gettempdir(), "manon-deploy.tar.gz")

    def _exclude(ti):
        if "__pycache__" in ti.name or ti.name.endswith(".pyc"):
            return None
        return ti

    with tarfile.open(tar_path, "w:gz") as tf:
        for sp in SYNC_PATHS:
            full = os.path.join(ROOT, sp)
            if os.path.exists(full):
                tf.add(full, arcname=sp, filter=_exclude)
    size_kb = os.path.getsize(tar_path) / 1024
    print(f"  {', '.join(SYNC_PATHS)} → {size_kb:.0f} KB")

    print("\n=== 上传 ===")
    remote_tar = "/tmp/manon-deploy.tar.gz"
    sftp = c.open_sftp()
    sftp.put(tar_path, remote_tar)
    sftp.close()
    os.remove(tar_path)

    print("\n=== 部署 ===")
    run(c, f"mkdir -p {REMOTE_DIR} && cd {REMOTE_DIR} && tar xzf {remote_tar} && rm -f {remote_tar}")
    # Strip Windows \r from Python files (tarball created on Windows has CRLF)
    run(c, f"find {REMOTE_DIR}/saas {REMOTE_DIR}/matrixone_graph -name '*.py' -exec sed -i 's/\\r$//' {{}} + 2>/dev/null || true")

    # Write VERSION file on server and locally
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode == 0:
        version = f"0.1.{result.stdout.strip()}"
        run(c, f"echo '{version}' > {REMOTE_DIR}/VERSION")
        # Also write locally so public sync picks it up
        with open(os.path.join(ROOT, "VERSION"), "w") as f:
            f.write(version + "\n")
        print(f"  VERSION: {version}")


def setup_systemd(c):
    """Install systemd service for saas."""
    print("\n=== 安装 systemd 服务 ===")
    sftp = c.open_sftp()
    with sftp.open("/etc/systemd/system/manon-saas.service", "w") as f:
        f.write(SYSTEMD_UNIT)
    sftp.close()
    run(c, "systemctl daemon-reload")
    run(c, "systemctl enable manon-saas")
    print("  manon-saas.service installed and enabled")


def restart(c):
    """Restart saas — kill old process first, prefer systemd, fallback to nohup."""
    print("\n=== 重启 saas ===")
    # Always kill existing process first to free the port
    run(c, "pgrep -f 'python3 -m saas' | xargs -r kill; sleep 2")

    status = run(c, "systemctl is-enabled manon-saas 2>/dev/null || echo 'not-found'")
    if "not-found" not in status:
        run(c, "systemctl restart manon-saas")
        time.sleep(4)
        out = run(c, "systemctl status manon-saas --no-pager -l | head -12")
        print(out)
    else:
        script = (
            f"cd {REMOTE_DIR} && nohup python3 -m saas > /tmp/saas.log 2>&1 & "
            "sleep 3; "
            "pgrep -f 'python3 -m saas' | head -1"
        )
        out = run(c, script)
        print(f"  PID: {out}")


def verify(c):
    """Health + version check."""
    print("\n=== 验证 ===")
    time.sleep(2)
    health = run(c, "curl -s http://localhost:3700/health")
    print(f"  health: {health}")
    version = run(c, "curl -s http://localhost:3700/version")
    print(f"  version: {version}")


def main():
    parser = argparse.ArgumentParser(description="Deploy manon to R760")
    parser.add_argument("--setup-systemd", action="store_true", help="Install systemd service")
    parser.add_argument("--auto", action="store_true", help="Only deploy if server files changed")
    args = parser.parse_args()

    if args.auto and not needs_deploy():
        print("服务端代码无变更，跳过部署。")
        return

    c = connect()
    try:
        deploy(c)
        if args.setup_systemd:
            setup_systemd(c)
        restart(c)
        verify(c)
        save_deploy_stamp()
    finally:
        c.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
