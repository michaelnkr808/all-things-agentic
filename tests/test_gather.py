"""Gathering logic (Michael).

The model calls are faked throughout: these assert on the gate, which path
runs, the cap, and the fallbacks — not on Gemini's judgment. No API key needed.
"""

import types as pytypes

import pytest

from agentic.contracts.config import (
    AgentType,
    AgentTypeConfig,
    DepartmentConfig,
    EnvironmentConfig,
    GathererLimits,
)
from agentic.contracts.messages import GatherRequest
from agentic.gatherers import gather, permissions


@pytest.fixture
def dept_root(tmp_path):
    """A finance department the analyst may read, and an hr one they may not."""
    finance = tmp_path / "finance"
    finance.mkdir()
    (finance / "budget_2026_final.csv").write_text(
        "team,line_item,budgeted_usd,actual_usd\nengineering,infra,50000,42000\n"
    )
    (finance / "offsite-menu.md").write_text("# Offsite catering\n\nTacos.\n")
    hr = tmp_path / "hr"
    hr.mkdir()
    (hr / "comp-bands.md").write_text("CONFIDENTIAL salary bands\n")
    return tmp_path


@pytest.fixture
def config(dept_root):
    return EnvironmentConfig(
        departments=[
            DepartmentConfig(
                name="finance",
                path=str(dept_root / "finance"),
                allowed_roles=["analyst", "admin"],
                file_globs=["**/*.md", "**/*.csv"],
            ),
            DepartmentConfig(
                name="hr",
                path=str(dept_root / "hr"),
                allowed_roles=["admin"],
                file_globs=["**/*.md"],
            ),
        ],
        gatherers=GathererLimits(
            max_gatherers=4, max_files_per_gatherer=3, overgather_factor=1.5
        ),
        agent_types={
            AgentType.FILE_GATHERER: AgentTypeConfig(model="gemini-3.5-flash", max_files=10)
        },
    )


@pytest.fixture
def perms():
    return permissions.PermissionsConfig(
        principal=permissions.Principal(role="analyst"),
        roles={
            "analyst": permissions.RoleAccess(departments=["finance"]),
            "admin": permissions.RoleAccess(departments=["finance", "hr"]),
        },
    )


def _request(department="finance", max_files=2):
    return GatherRequest(
        request_id="r0",
        department=department,
        query="what did we spend on infrastructure",
        max_files=max_files,
        requester_role="analyst",
    )


def _install(monkeypatch, *, assess=None, select=None, calls=None):
    """Fake the model. `assess`/`select` map a schema to the response to return."""

    async def generate_content(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        schema = kwargs["config"].response_schema
        if schema is gather._Picks:
            if select is None:
                raise AssertionError("selection ran but the test did not expect it")
            return pytypes.SimpleNamespace(parsed=select)
        if assess is None:
            raise AssertionError("assessment ran but the test did not expect it")
        return pytypes.SimpleNamespace(parsed=assess)

    monkeypatch.setattr(
        gather,
        "_client",
        lambda: pytypes.SimpleNamespace(
            aio=pytypes.SimpleNamespace(
                models=pytypes.SimpleNamespace(generate_content=generate_content)
            )
        ),
    )


def _assessments(*pairs):
    return gather._Assessments(
        assessments=[
            gather._Assessment(path=p, relevant=r, note=f"note for {p}") for p, r in pairs
        ]
    )


def _picks(*paths):
    return gather._Picks(picks=[gather._Pick(path=p, reason="because") for p in paths])


# --- the permission gate ----------------------------------------------------


async def test_denied_department_returns_nothing_and_records_the_denial(
    monkeypatch, config, perms
):
    _install(monkeypatch)
    result = await gather.gather(_request("hr"), config, perms)
    assert result.files == []
    assert result.denied == ["comp-bands.md"]


async def test_denied_content_never_reaches_the_model(monkeypatch, config, perms):
    """The prompt goes to a provider — gating after the fact would be too late."""
    calls = []
    _install(monkeypatch, calls=calls)
    await gather.gather(_request("hr"), config, perms)
    assert calls == []


# --- the read-everything path (the common case) -----------------------------


async def test_small_department_is_read_in_full_without_selection(
    monkeypatch, config, perms
):
    """2 files under a cap of 3: nothing may be filtered before reading."""
    calls = []
    _install(
        monkeypatch,
        assess=_assessments(("budget_2026_final.csv", True), ("offsite-menu.md", False)),
        calls=calls,
    )
    result = await gather.gather(_request(), config, perms)

    assert len(calls) == 1  # one call, and it was assessment not selection
    assert calls[0]["config"].response_schema is gather._Assessments
    # Both files' full text was sent, including the one later judged irrelevant.
    assert "engineering,infra,50000,42000" in calls[0]["contents"]
    assert "Tacos" in calls[0]["contents"]
    assert [f.path for f in result.files] == ["budget_2026_final.csv"]
    assert result.files[0].relevance_note == "note for budget_2026_final.csv"


async def test_relevance_note_comes_from_the_full_text_read(monkeypatch, config, perms):
    _install(monkeypatch, assess=_assessments(("budget_2026_final.csv", True)))
    result = await gather.gather(_request(), config, perms)
    assert result.files[0].relevance_note == "note for budget_2026_final.csv"
    assert "42000" in result.files[0].content


async def test_nothing_judged_relevant_returns_everything_read(
    monkeypatch, config, perms
):
    """A gap downstream is worse than noise — never hand back an empty department."""
    _install(
        monkeypatch,
        assess=_assessments(("budget_2026_final.csv", False), ("offsite-menu.md", False)),
    )
    result = await gather.gather(_request(), config, perms)
    assert {f.path for f in result.files} == {"budget_2026_final.csv", "offsite-menu.md"}


# --- the selection path (oversized departments) ------------------------------


async def test_oversized_department_selects_before_reading(monkeypatch, config, perms):
    """More files than the cap: selection runs first, then assessment."""
    finance = gather.Path(config.department("finance").path)
    for i in range(6):
        (finance / f"extra{i}.md").write_text(f"filler {i}\n")

    calls = []
    _install(
        monkeypatch,
        select=_picks("budget_2026_final.csv", "extra0.md"),
        assess=_assessments(("budget_2026_final.csv", True), ("extra0.md", True)),
        calls=calls,
    )
    result = await gather.gather(_request(), config, perms)

    assert len(calls) == 2
    assert calls[0]["config"].response_schema is gather._Picks
    assert calls[1]["config"].response_schema is gather._Assessments
    assert {f.path for f in result.files} == {"budget_2026_final.csv", "extra0.md"}


async def test_selection_respects_the_hard_ceiling(monkeypatch, config, perms):
    """max_files=10 * 1.5 = 15 wanted, but max_files_per_gatherer caps it at 3."""
    finance = gather.Path(config.department("finance").path)
    names = [f"extra{i}.md" for i in range(6)]
    for name in names:
        (finance / name).write_text("filler\n")

    _install(
        monkeypatch,
        select=_picks(*names),
        assess=_assessments(*[(n, True) for n in names]),
    )
    result = await gather.gather(_request(max_files=10), config, perms)
    assert len(result.files) == config.gatherers.max_files_per_gatherer


async def test_invented_path_is_rejected_without_a_read(monkeypatch, config, perms):
    finance = gather.Path(config.department("finance").path)
    for i in range(6):
        (finance / f"extra{i}.md").write_text("filler\n")

    _install(
        monkeypatch,
        select=_picks("../../etc/passwd", "budget_2026_final.csv"),
        assess=_assessments(("budget_2026_final.csv", True)),
    )
    result = await gather.gather(_request(), config, perms)
    assert [f.path for f in result.files] == ["budget_2026_final.csv"]
    assert any("unlisted path" in e for e in result.errors)


async def test_total_size_over_budget_triggers_selection(
    monkeypatch, config, perms
):
    """Few files, but too much text to read wholesale."""
    monkeypatch.setattr(gather, "READ_ALL_CHARS", 10)
    calls = []
    _install(
        monkeypatch,
        select=_picks("budget_2026_final.csv"),
        assess=_assessments(("budget_2026_final.csv", True)),
        calls=calls,
    )
    await gather.gather(_request(), config, perms)
    assert calls[0]["config"].response_schema is gather._Picks


# --- resilience -------------------------------------------------------------


async def test_assessment_failure_still_returns_the_files(monkeypatch, config, perms):
    """The files are already read — dropping them would be a silent gap."""

    async def explode(**kwargs):
        raise RuntimeError("503 unavailable")

    monkeypatch.setattr(
        gather,
        "_client",
        lambda: pytypes.SimpleNamespace(
            aio=pytypes.SimpleNamespace(
                models=pytypes.SimpleNamespace(generate_content=explode)
            )
        ),
    )
    result = await gather.gather(_request(), config, perms)
    assert {f.path for f in result.files} == {"budget_2026_final.csv", "offsite-menu.md"}
    assert any("returned unassessed" in e for e in result.errors)


async def test_selection_failure_falls_back_to_keyword_matching(
    monkeypatch, config, perms
):
    """Selection is the one stage with no local substitute."""
    finance = gather.Path(config.department("finance").path)
    for i in range(6):
        (finance / f"extra{i}.md").write_text("filler\n")

    async def explode(**kwargs):
        raise RuntimeError("503 unavailable")

    monkeypatch.setattr(
        gather,
        "_client",
        lambda: pytypes.SimpleNamespace(
            aio=pytypes.SimpleNamespace(
                models=pytypes.SimpleNamespace(generate_content=explode)
            )
        ),
    )
    result = await gather.gather(_request(), config, perms)
    assert any("used keyword match" in e for e in result.errors)


async def test_unreadable_file_does_not_sink_the_department(monkeypatch, config, perms):
    real_read = gather.Path.read_text

    def flaky(self, *args, **kwargs):
        if self.name == "offsite-menu.md":
            raise OSError("disk gremlin")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(gather.Path, "read_text", flaky)
    _install(monkeypatch, assess=_assessments(("budget_2026_final.csv", True)))
    result = await gather.gather(_request(), config, perms)
    assert [f.path for f in result.files] == ["budget_2026_final.csv"]
    assert any("disk gremlin" in e for e in result.errors)


async def test_empty_department_returns_an_empty_result(
    monkeypatch, config, perms, dept_root
):
    for f in (dept_root / "finance").iterdir():
        f.unlink()
    calls = []
    _install(monkeypatch, calls=calls)
    result = await gather.gather(_request(), config, perms)
    assert result.files == []
    assert calls == []


# --- cloud-backed departments -----------------------------------------------


@pytest.fixture
def cloud_config(config, dept_root):
    """Finance moved to GCS; its local dir is left empty on purpose."""
    from agentic.contracts.config import StorageConfig

    for f in (dept_root / "finance").iterdir():
        f.unlink()
    config.department("finance").storage = StorageConfig(
        provider="gcs", bucket="acme-finance", prefix=""
    )
    return config


def _fake_cloud_files(monkeypatch, files, denied=(), errors=()):
    """Stand in for Malik's adapter: returns what the backend would have."""
    from agentic.gatherers import gatherer
    from agentic.contracts.messages import GatheredFile, GatherResult

    async def fake_gather_files(request, config):
        return GatherResult(
            request_id=request.request_id,
            department=request.department,
            files=[
                GatheredFile(
                    path=path,
                    department=request.department,
                    content=content,
                    relevance_note="matched 2 keyword(s)",  # the note we replace
                )
                for path, content in files.items()
            ],
            denied=list(denied),
            errors=list(errors),
        )

    monkeypatch.setattr(gatherer, "gather_files", fake_gather_files)


async def test_storage_backed_department_is_routed_to_the_cloud_adapter(
    monkeypatch, cloud_config, perms
):
    """Without routing, a cloud department globs its empty local dir and returns nothing."""
    _fake_cloud_files(monkeypatch, {"q3-budget.csv": "team,actual\nengineering,42000\n"})
    _install(monkeypatch, assess=_assessments(("q3-budget.csv", True)))
    result = await gather.gather(_request(), cloud_config, perms)
    assert [f.path for f in result.files] == ["q3-budget.csv"]
    assert "42000" in result.files[0].content


async def test_cloud_files_get_assessed_notes_not_keyword_notes(
    monkeypatch, cloud_config, perms
):
    """A cloud file must be indistinguishable from a local one downstream."""
    _fake_cloud_files(monkeypatch, {"q3-budget.csv": "actual,42000\n"})
    _install(monkeypatch, assess=_assessments(("q3-budget.csv", True)))
    result = await gather.gather(_request(), cloud_config, perms)
    assert result.files[0].relevance_note == "note for q3-budget.csv"
    assert "keyword" not in result.files[0].relevance_note


async def test_cloud_content_is_regated_before_reaching_the_model(
    monkeypatch, cloud_config, perms
):
    """The adapter gates with permissions.check only; the full gate runs here."""
    calls = []
    _fake_cloud_files(monkeypatch, {"q3-budget.csv": "SENSITIVE CLOUD BODY"})
    _install(monkeypatch, calls=calls)

    # Principal loses the finance grant in the permissions config, while the
    # environment config still lists the role — check() would let this through.
    perms.roles["analyst"].departments = []
    result = await gather.gather(_request(), cloud_config, perms)

    assert calls == []  # no prompt was ever built
    assert result.files == []
    assert "q3-budget.csv" in result.denied


async def test_cloud_backend_errors_survive_into_the_result(
    monkeypatch, cloud_config, perms
):
    """A listing failure records an error instead of sinking the department."""
    _fake_cloud_files(monkeypatch, {}, errors=["cloud list failed: 403 Forbidden"])
    _install(monkeypatch)
    result = await gather.gather(_request(), cloud_config, perms)
    assert result.files == []
    assert any("cloud list failed" in e for e in result.errors)


async def test_cloud_assessment_failure_still_returns_the_files(
    monkeypatch, cloud_config, perms
):
    """Same resilience as the local path — the download already happened."""

    async def explode(**kwargs):
        raise RuntimeError("503 unavailable")

    _fake_cloud_files(monkeypatch, {"q3-budget.csv": "actual,42000\n"})
    monkeypatch.setattr(
        gather,
        "_client",
        lambda: pytypes.SimpleNamespace(
            aio=pytypes.SimpleNamespace(
                models=pytypes.SimpleNamespace(generate_content=explode)
            )
        ),
    )
    result = await gather.gather(_request(), cloud_config, perms)
    assert [f.path for f in result.files] == ["q3-budget.csv"]
    assert any("returned unassessed" in e for e in result.errors)
