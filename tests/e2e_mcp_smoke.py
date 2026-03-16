from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"C:\Users\zack_\AppData\Local\Programs\Python\Python312\python.exe")
RUNTIME_DIR = ROOT / "tests" / "e2e-runtime"
SAAS_LOG = RUNTIME_DIR / "saas.log"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ast import load_projects, save_projects


def _log(message: str) -> None:
    sys.stdout.write(f"[e2e {time.strftime('%H:%M:%S')}] {message}\n")
    sys.stdout.flush()


def _tool_text(result) -> str:
    parts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_repo_id(text: str) -> str:
    match = re.search(r"\(([0-9a-f]{8})\)", text)
    if not match:
        raise RuntimeError(f"repo_id not found in manon_init output:\n{text}")
    return match.group(1)


def _extract_marker(text: str, name: str) -> str:
    match = re.search(rf"<!-- {name}=(.*?) -->", text)
    if not match:
        raise RuntimeError(f"{name} marker not found in output:\n{text}")
    return match.group(1)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _embedding_for(text: str) -> list[float]:
    encoded = text.encode("utf-8")
    total = sum(encoded)
    return [
        float(len(text)),
        float(len(text.split())),
        float(total % 997),
        float((total // max(len(encoded), 1)) % 251),
    ]


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "manon-e2e-stub/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json({"ok": True})
            return
        self._write_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = self._read_json()
        if self.path == "/embed":
            inputs = payload.get("inputs", [])
            self._write_json([_embedding_for(text) for text in inputs])
            return

        if self.path == "/v1/chat/completions":
            analysis = {
                "sub_questions": ["What is MatrixoneGraph and where is it used?"],
                "covered": ["What is MatrixoneGraph and where is it used?"],
                "missing": [],
                "queries": [],
                "reason": "Initial context already covers the question.",
            }
            self._write_json({
                "choices": [{
                    "message": {
                        "content": json.dumps(analysis, ensure_ascii=False),
                    }
                }]
            })
            return

        self._write_json({"error": "not found"}, status=404)


def _start_stub_server(port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", port), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _wait_for_url(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0, trust_env=False)
            if response.status_code < 500:
                return
            last_error = f"{response.status_code}: {response.text}"
        except Exception as exc:  # pragma: no cover - transient startup path
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _register_tenant(base_url: str) -> str:
    response = httpx.post(
        f"{base_url}/api/v1/register",
        json={"name": "manon-e2e"},
        timeout=10.0,
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()["api_key"]


def _read_saas_log() -> str:
    if not SAAS_LOG.exists():
        return ""
    return SAAS_LOG.read_text(encoding="utf-8", errors="replace")


def _assert_nonempty_tool_output(name: str, text: str) -> None:
    preview = text.strip()
    _log(f"{name} output preview: {preview[:300] if preview else '<empty>'}")
    lowered = preview.lower()
    if not preview:
        raise RuntimeError(f"{name} returned empty output")
    if "repo not indexed yet" in lowered or "status=error" in lowered:
        raise RuntimeError(f"{name} returned failure output:\n{text}")
    if "未找到" in preview or "not found" in lowered:
        raise RuntimeError(f"{name} returned no-result output:\n{text}")


async def _run() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SAAS_LOG.write_text("", encoding="utf-8")
    project_key = str(ROOT.resolve()).replace("\\", "/")
    projects = load_projects()
    previous_project = projects["projects"].pop(project_key, None)
    save_projects(projects)
    _log(f"cleared existing local project binding: {'yes' if previous_project else 'no'}")

    embed_port = _find_free_port()
    llm_port = _find_free_port()
    saas_port = _find_free_port()
    _log(f"ports allocated: embedding={embed_port}, llm={llm_port}, saas={saas_port}")

    embedding_server, _ = _start_stub_server(embed_port)
    llm_server, _ = _start_stub_server(llm_port)
    _log("stub servers started")

    saas_env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        saas_env.pop(key, None)
    saas_env.update({
        "SAAS_DB_PATH": str(RUNTIME_DIR / "saas.db"),
        "SAAS_REPOS_DIR": str(RUNTIME_DIR / "repos"),
        "SAAS_INDEX_DIR": str(RUNTIME_DIR / "indexes"),
        "SAAS_DATA_DIR": str(RUNTIME_DIR / "data"),
        "SAAS_EMBEDDING_URL": f"http://127.0.0.1:{embed_port}",
        "SAAS_LLM_API_URL": f"http://127.0.0.1:{llm_port}/v1/chat/completions",
        "SAAS_LLM_API_KEY": "e2e-key",
    })

    with SAAS_LOG.open("w", encoding="utf-8") as saas_log:
        saas_proc = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "saas.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(saas_port),
        ],
        cwd=str(ROOT),
        env=saas_env,
        stdout=saas_log,
        stderr=subprocess.STDOUT,
        text=True,
        )

    try:
        _log("waiting for local SaaS health endpoint")
        _wait_for_url(f"http://127.0.0.1:{saas_port}/health")
        _log("local SaaS is healthy")
        api_key = _register_tenant(f"http://127.0.0.1:{saas_port}")
        _log("tenant registered")

        server = StdioServerParameters(
            command=str(PYTHON),
            args=["run_mcp.py"],
            cwd=str(ROOT),
            env={
                "MANON_API_URL": f"http://127.0.0.1:{saas_port}",
                "MANON_API_KEY": api_key,
            },
        )
        _log("starting MCP stdio session")
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                _log("MCP session initialized")

                _log("calling manon_init")
                init_result = await session.call_tool(
                    "manon_init",
                    {"project_path": str(ROOT)},
                    read_timeout_seconds=timedelta(seconds=60),
                )
                init_text = _tool_text(init_result)
                _log("manon_init completed")
                repo_id = _extract_repo_id(init_text)
                manon_dir = _extract_marker(init_text, "MANON_DIR")
                manon_python = _extract_marker(init_text, "MANON_PYTHON")
                _log(f"repo initialized: {repo_id}")

                _log("running local scan script")
                scan_proc = subprocess.run(
                    [manon_python, str(Path(manon_dir) / "scripts" / "manon-scan.py"), repo_id],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=600,
                )
                scan_summary = json.loads(scan_proc.stdout)
                _log(f"scan finished: {scan_summary}")

                _log("loading scan cache into MCP")
                scan_result = await session.call_tool(
                    "manon_scan_files",
                    {"repo_id": repo_id},
                    read_timeout_seconds=timedelta(seconds=60),
                )
                scan_payload = json.loads(_tool_text(scan_result))
                if scan_payload.get("total_batches", 0) != scan_summary.get("total_batches", 0):
                    raise RuntimeError(f"scan batch mismatch: {scan_payload} vs {scan_summary}")
                _log(f"scan cache loaded: {scan_payload}")

                _log("uploading scan batches")
                for batch_num in range(1, 201):
                    upload_result = await session.call_tool(
                        "manon_upload_batch",
                        {"repo_id": repo_id},
                        read_timeout_seconds=timedelta(seconds=120),
                    )
                    upload_payload = json.loads(_tool_text(upload_result))
                    _log(f"upload batch {batch_num}: {upload_payload}")
                    if upload_payload.get("status") == "done":
                        break
                    if upload_payload.get("status") == "error":
                        raise RuntimeError(f"upload failed: {upload_payload}")
                else:
                    raise RuntimeError("upload did not finish within 200 batches")

                _log("waiting for index status=done")
                for attempt in range(1, 61):
                    index_status_result = await session.call_tool(
                        "manon_index_status",
                        {"repo_id": repo_id},
                        read_timeout_seconds=timedelta(seconds=30),
                    )
                    index_status_text = _tool_text(index_status_result)
                    _log(f"index status poll {attempt}: {index_status_text.splitlines()[0] if index_status_text else '<empty>'}")
                    if "status: done" in index_status_text.lower() or "状态: done" in index_status_text.lower():
                        break
                    await anyio.sleep(2)
                else:
                    raise RuntimeError(f"repo did not reach done status:\n{index_status_text}")

                _log("calling manon_search")
                search_result = await session.call_tool(
                    "manon_search",
                    {"repo_id": repo_id, "query": "MatrixoneGraph", "top_k": 5, "depth": 1},
                    read_timeout_seconds=timedelta(seconds=30),
                )
                search_text = _tool_text(search_result)
                _assert_nonempty_tool_output("manon_search", search_text)
                _log("manon_search passed")

                _log("calling manon_graph")
                graph_result = await session.call_tool(
                    "manon_graph",
                    {"repo_id": repo_id, "symbol": "MatrixoneGraph", "depth": 1, "direction": "both"},
                    read_timeout_seconds=timedelta(seconds=30),
                )
                graph_text = _tool_text(graph_result)
                _assert_nonempty_tool_output("manon_graph", graph_text)
                _log("manon_graph passed")

                _log("calling manon_impact")
                impact_result = await session.call_tool(
                    "manon_impact",
                    {"repo_id": repo_id, "commit": "HEAD", "max_depth": 2},
                    read_timeout_seconds=timedelta(seconds=60),
                )
                impact_text = _tool_text(impact_result)
                _assert_nonempty_tool_output("manon_impact", impact_text)
                _log("manon_impact passed")

                _log("calling manon_deep_query")
                deep_query_result = await session.call_tool(
                    "manon_deep_query",
                    {
                        "repo_id": repo_id,
                        "question": "What is MatrixoneGraph and where is it used?",
                        "max_rounds": 1,
                    },
                    read_timeout_seconds=timedelta(seconds=90),
                )
                deep_query_text = _tool_text(deep_query_result)
                _assert_nonempty_tool_output("manon_deep_query", deep_query_text)
                _log("manon_deep_query passed")

                print(json.dumps({
                    "repo_id": repo_id,
                    "scan_summary": scan_summary,
                    "search_ok": True,
                    "graph_ok": True,
                    "impact_ok": True,
                    "deep_query_ok": True,
                    "saas_url": f"http://127.0.0.1:{saas_port}",
                }, ensure_ascii=False))
    except Exception as exc:
        output = _read_saas_log()
        if output:
            raise RuntimeError(f"{exc}\n\nLocal SaaS output:\n{output}") from None
        raise
    finally:
        projects = load_projects()
        if previous_project is not None:
            projects["projects"][project_key] = previous_project
        else:
            projects["projects"].pop(project_key, None)
        save_projects(projects)
        if saas_proc.poll() is None:
            saas_proc.terminate()
            try:
                saas_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                saas_proc.kill()
        embedding_server.shutdown()
        llm_server.shutdown()
        embedding_server.server_close()
        llm_server.server_close()


if __name__ == "__main__":
    anyio.run(_run)
