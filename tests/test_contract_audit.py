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


class TestSelfUse:
    def test_constant_used_only_inside_its_own_module_is_alive(self, tmp_path):
        _write(tmp_path, "config.py",
               'LOGGER = get_logger("x")\nDEAD = 1\n\ndef setup():\n    LOGGER.info("hi")\n')
        found = _findings(tmp_path, "configs")
        assert "const:config.py:LOGGER" not in found
        assert found["const:config.py:DEAD"]["verdict"] == "dead"

    def test_exported_constant_nothing_uses_is_still_dead(self, tmp_path):
        _write(tmp_path, "config.ts",
               "export const ENV_VARS = ['A', 'B']\n"
               "export const TIMEOUT_MS = 120\n"
               "export function build() { return { t: TIMEOUT_MS } }\n")
        found = _findings(tmp_path, "configs")
        assert found["const:config.ts:ENV_VARS"]["verdict"] == "dead"
        assert "const:config.ts:TIMEOUT_MS" not in found

    def test_mime_types_are_not_state_values(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE files (\n"
               "  media_type text NOT NULL DEFAULT 'application/octet-stream',\n"
               "  status text CHECK (status IN ('ready', 'archived'))\n);\n")
        _write(tmp_path, "app.py", "def ok(f):\n    return f['status'] == 'ready'\n")
        found = _findings(tmp_path, "states")
        assert not [k for k in found if "media_type" in k]
        assert found["state:files.status='archived'"]["verdict"] == "dead"

    def test_unreferenced_default_is_write_only_not_dead(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE entries (\n"
               "  status text NOT NULL DEFAULT 'posted' CHECK (status IN ('draft','posted'))\n);\n")
        _write(tmp_path, "app.py", "def draft(e):\n    return e['status'] == 'draft'\n")
        found = _findings(tmp_path, "states")
        assert found["state:entries.status='posted'"]["verdict"] == "suspect"
        assert "只写不读" in found["state:entries.status='posted'"]["summary"]


class TestSameFileCaller:
    def test_route_called_from_its_own_module_is_alive(self, tmp_path):
        """A sibling handler that hands the URL to the client is a real caller."""
        _write(tmp_path, "sharing.py",
               'router = APIRouter(prefix="/api/v1/employee")\n'
               '@router.post("/artifact-links")\n'
               'def mint(token):\n'
               '    return {"url": f"/api/v1/employee/artifact-links/{token}"}\n'
               '@router.get("/artifact-links/{raw_token}")\n'
               'def consume(raw_token): ...\n')
        found = _findings(tmp_path, "endpoints")
        assert "GET /api/v1/employee/artifact-links/{raw_token}" not in found

    def test_a_routes_own_registration_never_counts_as_its_caller(self, tmp_path):
        _write(tmp_path, "api.py",
               'router = APIRouter(prefix="/admin")\n'
               '@router.post(\n'
               '    "/pilot-access",\n'
               '    status_code=201,\n'
               ')\n'
               'def grant(): ...\n')
        found = _findings(tmp_path, "endpoints")
        assert found["POST /admin/pilot-access"]["verdict"] == "dead"


class TestPolicyIsNotEvidence:
    def test_policy_file_does_not_keep_surfaces_alive(self, tmp_path):
        """The exemption list names every id it exempts — and every id it doesn't."""
        _write(tmp_path, ".env.example", "DEAD_KNOB=1\n")
        _write(tmp_path, "api.py", 'router = APIRouter(prefix="/admin")\n'
                                   '@router.post("/pilot-access")\n'
                                   'def grant(): ...\n')
        _write(tmp_path, ".manon-contract.yaml",
               "# triage checklist, nothing active yet\n"
               "# - id: 'env:DEAD_KNOB'\n"
               "# - id: 'POST /admin/pilot-access'\n"
               "exempt:\n  configs: []\n")
        result = audit_project(str(tmp_path), tables=("configs", "endpoints"))
        ids = {f["id"] for f in result["findings"]}
        assert "env:DEAD_KNOB" in ids
        assert "POST /admin/pilot-access" in ids


# ── schema lifecycle ─────────────────────────────────────────────────────────

class TestSchemaLifecycle:
    """A schema is a sequence of migrations, not a snapshot of the first one."""

    def test_values_of_a_dropped_table_are_not_reported(self, tmp_path):
        _write(tmp_path, "migrations/003_ai.sql",
               "CREATE TABLE tool_action_requests (\n"
               "  status text CHECK (status IN ('pending', 'executed'))\n);\n")
        _write(tmp_path, "migrations/058_retire.sql",
               "DROP TABLE IF EXISTS tool_action_requests;\n")
        assert _findings(tmp_path, "states") == {}

    def test_a_table_recreated_after_the_drop_is_live_again(self, tmp_path):
        _write(tmp_path, "migrations/003_ai.sql",
               "CREATE TABLE jobs (\n  status text CHECK (status IN ('queued'))\n);\n")
        _write(tmp_path, "migrations/058_retire.sql", "DROP TABLE IF EXISTS jobs;\n")
        _write(tmp_path, "migrations/070_revive.sql",
               "CREATE TABLE jobs (\n  status text CHECK (status IN ('queued'))\n);\n")
        found = _findings(tmp_path, "states")
        assert found["state:jobs.status='queued'"]["verdict"] == "dead"

    def test_values_of_a_dropped_column_are_not_reported(self, tmp_path):
        _write(tmp_path, "migrations/003_prefs.sql",
               "CREATE TABLE prefs (\n  status text CHECK (status IN ('on', 'muted'))\n);\n")
        _write(tmp_path, "migrations/058_retire.sql",
               "ALTER TABLE prefs\n    DROP COLUMN IF EXISTS status;\n")
        assert _findings(tmp_path, "states") == {}

    def test_a_widening_migration_keeps_the_value_alive(self, tmp_path):
        _write(tmp_path, "migrations/003_req.sql",
               "CREATE TABLE requests (\n  status text CHECK (status IN ('new'))\n);\n")
        _write(tmp_path, "migrations/058_widen.sql",
               "ALTER TABLE requests\n"
               "    ADD CONSTRAINT requests_status_check CHECK (status IN ('new', 'held'));\n")
        _write(tmp_path, "app.py", "def hold(r):\n    r['status'] = 'held'\n")
        found = _findings(tmp_path, "states")
        assert "state:requests.status='held'" not in found


class TestSeedDataIsAWriter:
    def test_a_value_written_only_by_seed_sql_is_not_dead(self, tmp_path):
        """Schema lines declare; every other SQL line is a real reader or writer."""
        _write(tmp_path, "migrations/001_meetings.sql",
               "CREATE TABLE meetings (\n"
               "  preparation_status text CHECK (preparation_status IN ('ready', 'needs_input'))\n);\n")
        _write(tmp_path, "scripts/seed_demo.sql",
               "INSERT INTO meetings(preparation_status) VALUES ('needs_input');\n")
        found = _findings(tmp_path, "states")
        assert "state:meetings.preparation_status='needs_input'" not in found

    def test_the_declaration_itself_is_still_not_a_use(self, tmp_path):
        _write(tmp_path, "migrations/001_meetings.sql",
               "CREATE TABLE meetings (\n"
               "  preparation_status text CHECK (preparation_status IN ('ready', 'needs_input'))\n);\n")
        found = _findings(tmp_path, "states")
        assert found["state:meetings.preparation_status='needs_input'"]["verdict"] == "dead"


class TestWriteViolatesCheck:
    """The one verdict here that is a certainty: this statement cannot succeed."""

    def test_literal_outside_the_check_set_is_reported(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE service_heartbeats (\n"
               "  status text NOT NULL CHECK (status IN ('ready', 'degraded', 'stopped'))\n);\n")
        _write(tmp_path, "worker.py",
               "async def beat(c):\n"
               "    await c.execute('''\n"
               "        INSERT INTO service_heartbeats(service_name, status)\n"
               "        VALUES ($1, 'error')\n"
               "    ''', name)\n")
        found = _findings(tmp_path, "states")
        finding = found["write:service_heartbeats.status='error'"]
        assert finding["verdict"] == "dead"
        assert finding["where"].startswith("worker.py:")

    def test_a_declared_value_is_not_reported(self, tmp_path):
        _write(tmp_path, "schema.sql",
               "CREATE TABLE service_heartbeats (\n"
               "  status text NOT NULL CHECK (status IN ('ready', 'degraded'))\n);\n")
        _write(tmp_path, "worker.py",
               "async def beat(c):\n"
               "    await c.execute('''\n"
               "        INSERT INTO service_heartbeats(service_name, status)\n"
               "        VALUES ($1, 'ready')\n"
               "    ''', name)\n")
        assert not [k for k in _findings(tmp_path, "states") if k.startswith("write:")]

    def test_a_later_update_is_not_attributed_to_an_earlier_table(self, tmp_path):
        """A statement inside a host string has no `;`. Unbounded, it swallows the file."""
        _write(tmp_path, "schema.sql",
               "CREATE TABLE links (\n  status text CHECK (status IN ('active'))\n);\n"
               "CREATE TABLE contracts (\n  status text CHECK (status IN ('active','satisfied'))\n);\n")
        _write(tmp_path, "app.py",
               "async def advance(c):\n"
               "    await c.execute('''UPDATE links SET status=$1 WHERE id=$2''', s, i)\n"
               "    await c.execute('''UPDATE contracts SET status='satisfied' WHERE id=$1''', i)\n")
        assert not [k for k in _findings(tmp_path, "states") if k.startswith("write:")]

    def test_a_column_with_only_a_default_is_not_a_closed_set(self, tmp_path):
        """No CHECK means no allowed set, so no write can be proven wrong."""
        _write(tmp_path, "schema.sql",
               "CREATE TABLE jobs (\n  status text NOT NULL DEFAULT 'pending'\n);\n")
        _write(tmp_path, "app.py",
               "async def go(c):\n"
               "    await c.execute(\"INSERT INTO jobs(status) VALUES ('anything')\")\n")
        assert not [k for k in _findings(tmp_path, "states") if k.startswith("write:")]


class TestSettingsPrefix:
    """`env_prefix` binds an env name that appears nowhere in the source."""

    def test_prefixed_env_key_bound_to_a_field_is_alive(self, tmp_path):
        _write(tmp_path, ".env.example", "CASEOS_OUTBOX_WORKER_ENABLED=true\n")
        _write(tmp_path, "config.py",
               "class Settings(BaseSettings):\n"
               '    model_config = SettingsConfigDict(env_prefix="CASEOS_")\n'
               "    outbox_worker_enabled: bool = True\n")
        assert "env:CASEOS_OUTBOX_WORKER_ENABLED" not in _findings(tmp_path, "configs")

    def test_prefixed_key_with_no_matching_field_is_still_dead(self, tmp_path):
        """`CASEOS_PUBLIC_URL` next to a field named `public_app_url` is a decoy."""
        _write(tmp_path, ".env.example", "CASEOS_PUBLIC_URL=http://localhost\n")
        _write(tmp_path, "config.py",
               "class Settings(BaseSettings):\n"
               '    model_config = SettingsConfigDict(env_prefix="CASEOS_")\n'
               "    public_app_url: str = ''\n")
        found = _findings(tmp_path, "configs")
        assert found["env:CASEOS_PUBLIC_URL"]["verdict"] == "dead"

    def test_the_prefix_must_be_declared_to_apply(self, tmp_path):
        _write(tmp_path, ".env.example", "CASEOS_OUTBOX_WORKER_ENABLED=true\n")
        _write(tmp_path, "config.py",
               "class Settings(BaseSettings):\n    outbox_worker_enabled: bool = True\n")
        found = _findings(tmp_path, "configs")
        assert found["env:CASEOS_OUTBOX_WORKER_ENABLED"]["verdict"] == "dead"


class TestAddColumnDefault:
    def test_add_column_default_is_bound_to_the_column(self, tmp_path):
        """Unanchored, `ADD COLUMN x ... DEFAULT 'v'` binds the default to "ADD"."""
        _write(tmp_path, "migrations/035_artifacts.sql",
               "ALTER TABLE artifacts\n"
               "    ADD COLUMN source_type text NOT NULL DEFAULT 'legacy'\n"
               "        CHECK (source_type IN ('legacy', 'web_upload'));\n")
        found = _findings(tmp_path, "states")
        assert found["state:artifacts.source_type='legacy'"]["verdict"] == "suspect"
        assert found["state:artifacts.source_type='web_upload'"]["verdict"] == "dead"


class TestConstraintRedefinition:
    """`DROP CONSTRAINT, ADD CONSTRAINT` replaces the domain — it does not add to it."""

    def test_a_narrowing_migration_removes_the_old_value(self, tmp_path):
        _write(tmp_path, "migrations/020_beats.sql",
               "CREATE TABLE beats (\n"
               "  status text CHECK (status IN ('ready', 'degraded', 'stopped'))\n);\n")
        _write(tmp_path, "migrations/101_narrow.sql",
               "ALTER TABLE beats\n"
               "    DROP CONSTRAINT IF EXISTS beats_status_check,\n"
               "    ADD CONSTRAINT beats_status_check CHECK (status IN ('ready', 'error'));\n")
        _write(tmp_path, "app.py",
               "async def beat(c):\n"
               "    await c.execute(\"INSERT INTO beats(status) VALUES ('error')\")\n"
               "    await c.execute(\"INSERT INTO beats(status) VALUES ('ready')\")\n")
        found = _findings(tmp_path, "states")
        assert "state:beats.status='stopped'" not in found
        assert not [k for k in found if k.startswith("write:")]

    def test_a_redefinition_still_flags_a_value_nothing_uses(self, tmp_path):
        _write(tmp_path, "migrations/020_beats.sql",
               "CREATE TABLE beats (\n  status text CHECK (status IN ('ready'))\n);\n")
        _write(tmp_path, "migrations/101_narrow.sql",
               "ALTER TABLE beats\n"
               "    DROP CONSTRAINT IF EXISTS beats_status_check,\n"
               "    ADD CONSTRAINT beats_status_check CHECK (status IN ('ready', 'halted'));\n")
        found = _findings(tmp_path, "states")
        assert found["state:beats.status='halted'"]["verdict"] == "dead"


class TestScopePredicateIsNotADeclaration:
    def test_a_scope_check_branch_does_not_shrink_the_vocabulary(self, tmp_path):
        """One ALTER, two CHECKs: the domain, and a rule that mentions two of its values."""
        _write(tmp_path, "migrations/052_kinds.sql",
               "ALTER TABLE conversations\n"
               "    ADD CONSTRAINT conversations_kind_check\n"
               "        CHECK (kind IN ('main', 'chat', 'case'));\n")
        _write(tmp_path, "migrations/076_widen.sql",
               "ALTER TABLE conversations\n"
               "    DROP CONSTRAINT IF EXISTS conversations_kind_check,\n"
               "    DROP CONSTRAINT IF EXISTS conversations_scope_check,\n"
               "    ADD CONSTRAINT conversations_kind_check\n"
               "        CHECK (kind IN ('main', 'chat', 'case', 'task')),\n"
               "    ADD CONSTRAINT conversations_scope_check\n"
               "        CHECK (\n"
               "            (kind = 'task' AND case_id IS NOT NULL)\n"
               "            OR (kind IN ('main', 'chat') AND case_id IS NULL)\n"
               "        );\n")
        _write(tmp_path, "app.py",
               "async def open_task(c):\n"
               "    await c.execute(\"INSERT INTO conversations(kind) VALUES ('task')\")\n"
               "    await c.execute(\"INSERT INTO conversations(kind) VALUES ('case')\")\n")
        found = _findings(tmp_path, "states")
        assert not [k for k in found if k.startswith("write:")]


class TestPathTokensNotSubstrings:
    """`new-api-latest` holds "test" as a substring and no test token.

    Substring matching drops every file under such a directory to the test tier,
    and each table then reports live production code as "only mentioned in
    tests". It is silent: the run is green, the verdicts are wrong, and the
    wrongness always points the same way — toward deleting something alive.
    """

    def test_a_directory_merely_containing_test_is_not_a_test(self, tmp_path):
        _write(tmp_path, "new-api-latest/.env.example", "PROXY_ENCRYPTION_KEY=x\n")
        _write(tmp_path, "new-api-latest/crypto.go",
               'var key = os.Getenv("PROXY_ENCRYPTION_KEY")\n')
        assert _findings(tmp_path, "configs") == {}

    def test_a_real_test_file_is_still_weak_evidence(self, tmp_path):
        _write(tmp_path, "new-api-latest/.env.example", "PROXY_ENCRYPTION_KEY=x\n")
        _write(tmp_path, "new-api-latest/crypto_test.go",
               'var key = os.Getenv("PROXY_ENCRYPTION_KEY")\n')
        found = _findings(tmp_path, "configs")
        assert found["env:PROXY_ENCRYPTION_KEY"]["verdict"] == "suspect"


class TestHttpClientIsNotARouter:
    """An axios instance named `API` is a caller, not a route table."""

    def test_axios_instance_call_sites_stay_callers(self, tmp_path):
        _write(tmp_path, "router.go",
               'func Set(r *gin.Engine) {\n'
               '\tapi := r.Group("/api")\n'
               '\tapi.GET("/user/logout", controller.Logout)\n'
               '}\n')
        _write(tmp_path, "web/helpers/api.js", "export let API = axios.create({})\n")
        _write(tmp_path, "web/header.js", "await API.get('/api/user/logout')\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_an_express_router_named_api_still_declares(self, tmp_path):
        _write(tmp_path, "server.js",
               "const api = express.Router()\n"
               "api.post('/pilot-access', grant)\n")
        found = _findings(tmp_path, "endpoints")
        assert found["POST /pilot-access"]["verdict"] == "dead"


class TestGoRoutes:
    """Without a Go branch a Gin backend contributes zero routes to the table."""

    def test_gin_route_with_no_caller_is_dead(self, tmp_path):
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tapiRouter := r.Group("/api")\n'
               '\tadmin := apiRouter.Group("/super-admin")\n'
               '\tadmin.POST("/pilot-access", controller.Grant)\n'
               '}\n')
        found = _findings(tmp_path, "endpoints")
        assert found["POST /api/super-admin/pilot-access"]["verdict"] == "dead"

    def test_gin_route_called_from_the_frontend_is_clean(self, tmp_path):
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tapiRouter := r.Group("/api")\n'
               '\tadmin := apiRouter.Group("/super-admin")\n'
               '\tadmin.POST("/pilot-access", controller.Grant)\n'
               '}\n')
        _write(tmp_path, "web/ui.jsx", "await post('/api/super-admin/pilot-access')\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_go_http_client_call_is_not_a_registration(self, tmp_path):
        """`client.Get(...)` is mixed case; only an upper-case verb registers."""
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tapiRouter := r.Group("/api")\n'
               '\tapiRouter.POST("/pilot-access", controller.Grant)\n'
               '}\n')
        _write(tmp_path, "probe/check.go",
               'resp, _ := client.Get("/api/pilot-access")\n')
        assert _findings(tmp_path, "endpoints") == {}


class TestPatternExemptions:
    def test_a_glob_exempts_the_family(self, tmp_path):
        _write(tmp_path, "router/relay.go",
               'func Set(r *gin.Engine) {\n'
               '\tv1 := r.Group("/v1")\n'
               '\tv1.POST("/messages", relay.Messages)\n'
               '\tv1.POST("/embeddings", relay.Embeddings)\n'
               '}\n')
        _write(tmp_path, ".manon-contract.yaml",
               'exempt:\n'
               '  endpoints:\n'
               '    "POST /v1/*": "relay 公共 API，消费者是客户不是本仓"\n')
        found = _findings(tmp_path, "endpoints")
        assert set(found) == {"POST /v1/messages", "POST /v1/embeddings"}
        assert all(f["exempt_reason"] for f in found.values())

    def test_an_exact_id_wins_over_a_glob(self, tmp_path):
        _write(tmp_path, "router/relay.go",
               'func Set(r *gin.Engine) {\n'
               '\tv1 := r.Group("/v1")\n'
               '\tv1.POST("/messages", relay.Messages)\n'
               '}\n')
        _write(tmp_path, ".manon-contract.yaml",
               'exempt:\n'
               '  endpoints:\n'
               '    "POST /v1/messages": "逐条判定过"\n'
               '    "POST /v1/*": "兜底"\n')
        found = _findings(tmp_path, "endpoints")
        assert found["POST /v1/messages"]["exempt_reason"] == "逐条判定过"

    def test_a_glob_matching_nothing_is_reported_stale(self, tmp_path):
        _write(tmp_path, "router/relay.go",
               'func Set(r *gin.Engine) {\n'
               '\tv1 := r.Group("/v1")\n'
               '\tv1.POST("/messages", relay.Messages)\n'
               '}\n')
        _write(tmp_path, ".manon-contract.yaml",
               'exempt:\n'
               '  endpoints:\n'
               '    "POST /v9/*": "早就删干净了"\n')
        result = audit_project(str(tmp_path), tables=("endpoints",))
        assert {"table": "endpoints", "id": "POST /v9/*", "reason": "早就删干净了"} \
            in result["stale_exemptions"]


class TestRoutesDeclaredInTestsAreNotSurfaces:
    def test_a_harness_route_is_not_collected(self, tmp_path):
        _write(tmp_path, "middleware/auth_harness_test.go",
               'func harness() *gin.Engine {\n'
               '\tr := gin.New()\n'
               '\tr.GET("/probe", handler)\n'
               '\treturn r\n'
               '}\n')
        assert _findings(tmp_path, "endpoints") == {}

    def test_the_product_route_it_exercises_is_still_collected(self, tmp_path):
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tr.GET("/pilot-access", controller.Show)\n'
               '}\n')
        _write(tmp_path, "middleware/auth_harness_test.go",
               'func harness() *gin.Engine {\n'
               '\tr := gin.New()\n'
               '\tr.GET("/probe", handler)\n'
               '\treturn r\n'
               '}\n')
        found = _findings(tmp_path, "endpoints")
        assert set(found) == {"GET /pilot-access"}


class TestSingleSegmentPathWithQuery:
    def test_a_baseurl_client_sending_one_segment_plus_query_is_a_caller(self, tmp_path):
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tg := r.Group("/insights/api")\n'
               '\tg.GET("/init", controller.Init)\n'
               '}\n')
        _write(tmp_path, "web/panel.jsx",
               "const r = await API.get(`${INSIGHTS_API}/init?days=${days}`)\n")
        assert _findings(tmp_path, "endpoints") == {}

    def test_a_different_route_with_a_query_is_still_dead(self, tmp_path):
        _write(tmp_path, "router/api.go",
               'func Set(r *gin.Engine) {\n'
               '\tg := r.Group("/insights/api")\n'
               '\tg.GET("/init", controller.Init)\n'
               '\tg.GET("/prompts", controller.Prompts)\n'
               '}\n')
        _write(tmp_path, "web/panel.jsx",
               "const r = await API.get(`${INSIGHTS_API}/init?days=${days}`)\n")
        found = _findings(tmp_path, "endpoints")
        assert found["GET /insights/api/prompts"]["verdict"] == "dead"
