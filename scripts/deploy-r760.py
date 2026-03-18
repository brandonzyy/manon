"""Deploy saas + matrixone_graph to R760 server.

Usage:
  python scripts/deploy-r760.py              # 强制部署
  python scripts/deploy-r760.py --auto       # 仅服务端文件有变更时部署
  python scripts/deploy-r760.py --setup-systemd  # 首次安装 systemd 服务

Since R760 cannot reach GitHub, this script pushes code from local.
Only syncs the directories the server actually needs:
  saas/, matrixone_graph/, application/, core/, codeindex/, requirements.txt
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
KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_DIR = "/root/manon"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only sync what the server needs to run `python3 -m saas`
SYNC_PATHS = ["saas", "matrixone_graph", "application", "core", "codeindex", "requirements.txt"]
DEPLOY_STAMP = os.path.join(ROOT, ".last-deploy-hash")

SYSTEMD_UNIT = """\
[Unit]
Description=Manon SaaS API
After=network.target
# Stop restart loop after 5 failures in 60s — prevents endless cycle when port is stuck
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
WorkingDirectory=/root/manon
# Kill any process holding port 3700 before starting (handles orphans from SSH sessions)
ExecStartPre=-/usr/bin/fuser -k 3700/tcp
ExecStartPre=-/bin/sleep 1
ExecStart=/usr/bin/python3 -m saas
Restart=on-failure
RestartSec=3
# Kill the entire process group on stop, not just the main PID
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=10
Environment=PYTHONUNBUFFERED=1
Environment=SAAS_EMBEDDING_URL=http://172.16.15.21:9624
Environment=SAAS_LLM_API_URL=https://api.matrixone.online/v1/chat/completions
Environment=SAAS_LLM_MODEL=glm-4.7-fp8
Environment=SAAS_LLM_API_KEY=sk-cFnE5mi2zKEiWZa89TdFJcQQVKO4BTgSgz8E5EGOIpEpCpXJ
Environment=SAAS_ADMIN_SECRET=XWhc4E2VLkkv4QmqKSYtvlfCtpr4kgbCR9j2tMLdUdc

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
    c.connect(HOST, port=PORT, username=USER, key_filename=KEY_PATH, timeout=15)
    return c


def run(c, cmd, timeout=30):
    """Run command on server, return stdout."""
    _, so, se = c.exec_command(cmd, timeout=timeout)
    out = so.read().decode().strip()
    err = se.read().decode().strip()
    if err:
        print(f"  STDERR: {err}")
    return out


def wait_for_service(c, service_name, *, attempts=15, delay=2):
    """Poll systemd until the service becomes active."""
    for _ in range(attempts):
        status = run(c, f"systemctl is-active {service_name} 2>/dev/null || true", timeout=10)
        if status.strip() == "active":
            return True
        time.sleep(delay)
    return False


def wait_for_http(c, url, *, attempts=15, delay=2):
    """Poll an HTTP/HTTPS endpoint until it returns a body."""
    for _ in range(attempts):
        body = run(c, f"curl -kfsS {url} 2>/dev/null || true", timeout=10)
        if body:
            return body
        time.sleep(delay)
    return ""


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

    # Write VERSION file on server (read from local VERSION file)
    version_file = os.path.join(ROOT, "VERSION")
    if os.path.exists(version_file):
        version = open(version_file).read().strip()
    else:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=ROOT, capture_output=True, text=True,
        )
        version = f"1.0.{result.stdout.strip()}" if result.returncode == 0 else "1.0.0"
    run(c, f"echo '{version}' > {REMOTE_DIR}/VERSION")
    print(f"  VERSION: {version}")


def ensure_ssl_cert(c):
    """Generate self-signed SSL cert on server if not present."""
    ssl_dir = f"{REMOTE_DIR}/ssl"
    cert = f"{ssl_dir}/cert.pem"
    key = f"{ssl_dir}/key.pem"
    exists = run(c, f"test -f {cert} && test -f {key} && echo ok || echo missing")
    if exists.strip() == "ok":
        print("  SSL cert already exists, skipping")
        return
    print("  Generating self-signed SSL cert...")
    run(c, f"mkdir -p {ssl_dir}")
    run(c, (
        f"openssl req -x509 -newkey rsa:2048"
        f" -keyout {key} -out {cert}"
        f" -days 3650 -nodes"
        f" -subj '/CN=saas.matrixone.online'"
    ), timeout=30)
    print("  SSL cert generated")


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
    """Restart saas via systemd. Requires manon-saas.service to be installed."""
    print("\n=== 重启 saas ===")
    status = run(c, "systemctl is-enabled manon-saas 2>/dev/null || echo 'not-found'")
    if "not-found" in status:
        raise RuntimeError(
            "manon-saas.service not found — run with --setup-systemd first"
        )

    # 1. Stop cleanly so Restart=always doesn't race against us.
    run(c, "systemctl stop manon-saas 2>/dev/null || true", timeout=10)
    # 2. Kill anything still holding the port (nohup leftovers, etc.).
    run(c, "fuser -k 3700/tcp 2>/dev/null || true; sleep 1")
    # 3. Clear failure counter so StartLimitBurst doesn't block a fresh start.
    run(c, "systemctl reset-failed manon-saas 2>/dev/null || true")
    # 4. Start fresh.
    run(c, "systemctl start manon-saas", timeout=20)
    if not wait_for_service(c, "manon-saas", attempts=20, delay=2):
        print("  WARN: manon-saas did not report active within the wait window")
    out = run(c, "systemctl status manon-saas --no-pager -l | head -12")
    print(out)


def verify(c):
    """Health + version check on HTTP 3700."""
    print("\n=== 验证 ===")
    health = wait_for_http(c, "http://localhost:3700/health", attempts=20, delay=2)
    if not health:
        print("  WARN: health endpoint did not recover in time")
        return False
    print(f"  health: {health}")

    version = wait_for_http(c, "http://localhost:3700/version", attempts=10, delay=1)
    if not version:
        print("  WARN: version endpoint did not recover in time")
        return False
    print(f"  version: {version}")
    return True


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
        ensure_ssl_cert(c)

        # Always update systemd if it's already installed
        status = run(c, "systemctl is-enabled manon-saas 2>/dev/null || echo 'not-found'")
        if "not-found" not in status or args.setup_systemd:
            setup_systemd(c)

        restart(c)
        if verify(c):
            save_deploy_stamp()
        else:
            print("\nWARN: deployment finished but verification did not pass")
    finally:
        c.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
