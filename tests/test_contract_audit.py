"""Contract audit — every verdict has a positive and a negative case.

The audit is a gate. A gate that only ever says "clean" is indistinguishable
from a gate that is broken, so each table is tested both for what it catches and
for what it must refuse to flag.
"""

from __future__ import annotations

import json

import pytest

from core.contract_audit import audit_project
from core.contract_audit.files import enumerate_files
from core.contract_audit.policy import load_policy


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _findings(root, table):
    result = audit_project(str(root), tables=(table,))
    return {f["id"]: f for f in result["findings"]}


# ── endpoints ────────────────────────────────────────────────────────────────

class TestEndpoints:
    def test_route_with_no_caller_is_dead(self, tmp_path):
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/admin")\n'
                                   '@router.post("/pilot-access")\n'
                                   'def grant(): ...\n')
        found = _findings(tmp_path, "endpoints")
        assert found["POST /admin/pilot-access"]["verdict"] == "dead"

    def test_route_called_from_frontend_is_clean(self, tmp_path):
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/admin")\n'
                                   '@router.post("/pilot-access")\n'
                                   'def grant(): ...\n')
        _write(tmp_path, "ui.ts", "await fetch('/admin/pilot-access', {method:'POST'})\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_caller_holding_only_the_tail_still_counts(self, tmp_path):
        """A client with a baseURL sends `/pilot-access`, not the whole path."""
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/api/v1/admin/orgs")\n'
                                   '@router.get("/pilot-access")\n'
                                   'def show(): ...\n')
        _write(tmp_path, "ui.ts", "client.get(`/pilot-access`)\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_interpolated_url_still_counts(self, tmp_path):
        """Template literals never close as clean string literals."""
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/api/v1")\n'
                                   '@router.get("/cases/{case_id}/tasks")\n'
                                   'def tasks(): ...\n')
        _write(tmp_path, "ui.ts",
               "const u = `/api/v1/cases/${encodeURIComponent(id)}/tasks${x ? `/${x}` : ''}`\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_generated_spec_is_not_a_caller(self, tmp_path):
        """openapi.json is derived from the backend; it cannot keep it alive."""
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/admin")\n'
                                   '@router.post("/pilot-access")\n'
                                   'def grant(): ...\n'
                                   '@router.get("/legal-config")\n'
                                   'def legal(): ...\n')
        _write(tmp_path, "openapi.json", json.dumps(
            {"paths": {"/admin/pilot-access": {}, "/admin/legal-config": {}}}))
        found = _findings(tmp_path, "endpoints")
        assert found["POST /admin/pilot-access"]["verdict"] == "dead"

    def test_test_only_caller_is_suspect_not_dead(self, tmp_path):
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/admin")\n'
                                   '@router.post("/pilot-access")\n'
                                   'def grant(): ...\n')
        _write(tmp_path, "tests/test_api.py", "client.post('/admin/pilot-access')\n")
        found = _findings(tmp_path, "endpoints")
        assert found["POST /admin/pilot-access"]["verdict"] == "suspect"

    def test_sibling_route_does_not_keep_a_deeper_one_alive(self, tmp_path):
        """`/cases` must not read as a call to `/cases/{id}/archive`."""
        _write(tmp_path, "api.py", '@app.get("/cases")\n'
                                   'def index(): ...\n'
                                   '@app.post("/cases/{case_id}/archive")\n'
                                   'def archive(): ...\n')
        _write(tmp_path, "ui.ts", "await fetch('/cases')\n")
        found = _findings(tmp_path, "endpoints")
        assert found["POST /cases/{case_id}/archive"]["verdict"] == "dead"


# ── configs ──────────────────────────────────────────────────────────────────

class TestConfigs:
    def test_declared_but_unread_env_key_is_dead(self, tmp_path):
        _write(tmp_path, ".env.example", "OUTBOX_WORKER_ENABLED=true\nDB_URL=postgres://x\n")
        _write(tmp_path, "app.py", 'import os\nos.environ.get("DB_URL")\n')
        found = _findings(tmp_path, "configs")
        assert found["env:OUTBOX_WORKER_ENABLED"]["verdict"] == "dead"
        assert "env:DB_URL" not in found

    def test_key_read_through_a_settings_class_is_not_dead(self, tmp_path):
        """The reader side must be token-based; `os.environ` patterns miss ORM-ish config."""
        _write(tmp_path, ".env.example", "DB_URL=postgres://x\n")
        _write(tmp_path, "app.py", "class Settings(BaseSettings):\n    DB_URL: str = ''\n")
        assert _findings(tmp_path, "configs") == {}

    def test_unreferenced_config_constant_is_dead(self, tmp_path):
        _write(tmp_path, "config.py", 'JUDGE_API_KEY = ""\nDB_URL = ""\n')
        _write(tmp_path, "app.py", "from config import DB_URL\nprint(DB_URL)\n")
        found = _findings(tmp_path, "configs")
        assert found["const:config.py:JUDGE_API_KEY"]["verdict"] == "dead"
        assert "const:config.py:DB_URL" not in found

    def test_key_kept_alive_only_by_deploy_scripts_is_suspect(self, tmp_path):
        _write(tmp_path, ".env.example", "FEISHU_APP_SECRET=x\n")
        _write(tmp_path, "deploy/prepare_env.sh", 'echo "$FEISHU_APP_SECRET"\n')
        found = _findings(tmp_path, "configs")
        assert found["env:FEISHU_APP_SECRET"]["verdict"] == "suspect"


# ── states ───────────────────────────────────────────────────────────────────

class TestStates:
    def test_state_value_never_mentioned_is_dead(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE tickets (\n"
               "  status text CHECK (status IN ('open', 'resolved', 'archived'))\n);\n")
        _write(tmp_path, "app.py", "def close(t):\n    t.status = 'resolved'\n"
                                   "    if t.status == 'open':\n        pass\n")
        found = _findings(tmp_path, "states")
        assert found["state:tickets.status='archived'"]["verdict"] == "dead"
        assert "state:tickets.status='resolved'" not in found

    def test_value_only_ever_compared_is_a_phantom(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE changes (\n"
               "  status text CHECK (status IN ('merged', 'reverted'))\n);\n")
        _write(tmp_path, "app.py",
               "def is_done(change):\n"
               "    if change['status'] in {'merged', 'reverted'}:\n        return True\n")
        _write(tmp_path, "writer.py", "def merge(c):\n    c['status'] = 'merged'\n")
        found = _findings(tmp_path, "states")
        assert "幻想状态" in found["state:changes.status='reverted'"]["summary"]
        assert "state:changes.status='merged'" not in found

    def test_parameterized_writer_does_not_manufacture_a_phantom(self, tmp_path):
        """`SET status = $1` carries no literal; a lone assignment must still count."""
        _write(tmp_path, "schema.sql",
               "CREATE TABLE runs (\n  state text CHECK (state IN ('queued', 'running'))\n);\n")
        _write(tmp_path, "app.py",
               "TERMINAL = 'running'\n"
               "async def start(c, i):\n"
               "    await c.execute('UPDATE runs SET state = $1', TERMINAL)\n"
               "    if i['state'] == 'queued':\n        pass\n")
        found = _findings(tmp_path, "states")
        assert "state:runs.state='running'" not in found

    def test_column_default_is_written_by_the_database(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE jobs (\n"
               "  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done'))\n);\n")
        _write(tmp_path, "app.py", "def wait(j):\n    return j['status'] == 'pending'\n")
        found = _findings(tmp_path, "states")
        assert "state:jobs.status='pending'" not in found


# ── envelope ─────────────────────────────────────────────────────────────────

_GUARDED = "\n".join(
    f'@router.get("/safe{n}")\n'
    f"async def safe{n}(user = Depends(require_admin)):\n    return 1\n"
    for n in range(6)
)


class TestEnvelope:
    def test_ungated_entry_reaching_a_sink_is_reported(self, tmp_path):
        _write(tmp_path, "api.py", _GUARDED +
               '\n@router.post("/card-action")\n'
               "async def card_action(payload):\n"
               "    return transfer_funds(payload)\n"
               "\n\ndef transfer_funds(p):\n    return p\n")
        found = _findings(tmp_path, "envelope")
        assert found["envelope:api.py:card_action"]["verdict"] == "suspect"
        assert "transfer_funds" in found["envelope:api.py:card_action"]["summary"]

    def test_gated_entry_reaching_a_sink_is_clean(self, tmp_path):
        _write(tmp_path, "api.py", _GUARDED +
               '\n@router.post("/card-action")\n'
               "async def card_action(payload, user = Depends(require_admin)):\n"
               "    return transfer_funds(payload)\n"
               "\n\ndef transfer_funds(p):\n    return p\n")
        assert _findings(tmp_path, "envelope") == {}

    def test_ungated_entry_reaching_nothing_dangerous_is_clean(self, tmp_path):
        _write(tmp_path, "api.py", _GUARDED +
               '\n@router.get("/ping")\n'
               "async def ping():\n    return render_status()\n"
               "\n\ndef render_status():\n    return 'ok'\n")
        assert _findings(tmp_path, "envelope") == {}

    def test_db_execute_is_not_a_sink(self, tmp_path):
        """`exec` must not swallow `connection.execute`, or every row is a DB call."""
        _write(tmp_path, "api.py", _GUARDED +
               '\n@router.get("/rows")\n'
               "async def rows(connection):\n    return await connection.execute('SELECT 1')\n")
        assert _findings(tmp_path, "envelope") == {}

    def test_repo_without_an_auth_convention_is_skipped(self, tmp_path):
        _write(tmp_path, "api.py",
               '@router.post("/a")\n'
               "async def a(p):\n    return delete_everything(p)\n"
               "\n\ndef delete_everything(p):\n    return p\n")
        result = audit_project(str(tmp_path), tables=("envelope",))
        assert result["findings"] == []
        assert "无可违反的鉴权约定" in result["tables"][0]["note"]


# ── policy ───────────────────────────────────────────────────────────────────

class TestPolicy:
    def test_exemption_suppresses_a_finding(self, tmp_path):
        _write(tmp_path, ".env.example", "DEAD_KNOB=1\n")
        _write(tmp_path, ".manon-contract.yaml",
               "exempt:\n  configs:\n    - id: 'env:DEAD_KNOB'\n      reason: 'ops only'\n")
        result = audit_project(str(tmp_path), tables=("configs",))
        assert result["dead"] == 0
        assert result["exempted"] == 1
        assert result["findings"][0]["exempt_reason"] == "ops only"

    def test_exemption_that_matches_nothing_is_reported_as_stale(self, tmp_path):
        _write(tmp_path, ".env.example", "LIVE=1\n")
        _write(tmp_path, "app.py", 'import os\nos.environ["LIVE"]\n')
        _write(tmp_path, ".manon-contract.yaml",
               "exempt:\n  configs:\n    - id: 'env:GONE'\n      reason: 'retired'\n")
        result = audit_project(str(tmp_path), tables=("configs",))
        assert [e["id"] for e in result["stale_exemptions"]] == ["env:GONE"]

    def test_broken_policy_file_does_not_disable_the_audit(self, tmp_path):
        _write(tmp_path, ".env.example", "DEAD_KNOB=1\n")
        _write(tmp_path, ".manon-contract.yaml", "exempt: [oops\n")
        result = audit_project(str(tmp_path), tables=("configs",))
        assert result["dead"] == 1
        assert "解析失败" in result["policy_source"]


# ── enumeration ──────────────────────────────────────────────────────────────

class TestEnumeration:
    def test_worktrees_and_vendor_are_never_counted(self, tmp_path):
        _write(tmp_path, "app.py", "x = 1\n")
        _write(tmp_path, ".worktrees/copy/app.py", "x = 1\n")
        _write(tmp_path, "vendor/lib/app.py", "x = 1\n")
        _write(tmp_path, "node_modules/pkg/index.js", "x = 1\n")
        rels = {f.rel for f in enumerate_files(tmp_path, [])}
        assert rels == {"app.py"}

    def test_tool_scripts_are_in_scope(self, tmp_path):
        """The graph drops `scripts/`; the audit must not — gates live there."""
        _write(tmp_path, "scripts/check_gate.sh", "echo hi\n")
        rels = {f.rel for f in enumerate_files(tmp_path, [])}
        assert "scripts/check_gate.sh" in rels

    def test_custom_excludes_are_honoured(self, tmp_path):
        _write(tmp_path, "app.py", "x = 1\n")
        _write(tmp_path, "generated/out.py", "x = 1\n")
        rels = {f.rel for f in enumerate_files(tmp_path, ["**/generated/**"])}
        assert rels == {"app.py"}


class TestPolicyDefaults:
    def test_missing_policy_file_yields_defaults(self, tmp_path):
        policy = load_policy(tmp_path)
        assert policy.source == ""
        assert "status" in policy.state_columns


@pytest.mark.parametrize("table", ["endpoints", "configs", "states", "envelope"])
def test_empty_tree_produces_no_findings(tmp_path, table):
    assert audit_project(str(tmp_path), tables=(table,))["findings"] == []
