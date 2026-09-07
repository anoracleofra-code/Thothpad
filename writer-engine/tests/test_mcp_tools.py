import pytest

from backend.mcp_server import tool_call


def test_mcp_list_profiles():
    result = tool_call("prose_list_profiles", {})
    assert "profiles" in result


def test_mcp_diagnose():
    result = tool_call("prose_diagnose", {"text": "It wasn't fear. It was memory."})
    assert result["mode"] == "diagnose"


def test_mcp_manuscript_analysis():
    result = tool_call(
        "prose_analyze_manuscript",
        {
            "documents": [
                {"name": "one.md", "text": "Mara checked the ledger twice."},
                {"name": "two.md", "text": "Mara checked the ledger again."},
            ]
        },
    )
    assert result["mode"] == "manuscript"
    assert result["document_count"] == 2


def test_mcp_rewrite_rejects_boolean_and_fractional_passes():
    from backend import config

    expected = f"passes must be between 1 and {config.MAX_PASSES}"
    with pytest.raises(ValueError, match=expected):
        tool_call("prose_rewrite", {"text": "Draft", "passes": True})
    with pytest.raises(ValueError, match=expected):
        tool_call("prose_rewrite", {"text": "Draft", "passes": 1.7})


def test_mcp_rewrite_rejects_unsupported_mode():
    with pytest.raises(ValueError, match="unsupported rewrite mode"):
        tool_call("prose_rewrite", {"text": "Draft", "mode": "bogus"})


def test_documented_tool_lists_match_mcp_tools():
    from pathlib import Path

    from backend.mcp_server import TOOLS
    from backend.projects import agent_setup

    tool_names = [tool["name"] for tool in TOOLS]
    assert len(tool_names) == 11

    readme = Path(__file__).resolve().parents[1] / "README.md"
    documented = [
        line[2:].strip().strip("`")
        for line in readme.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `prose_")
    ]
    assert documented == tool_names
    assert agent_setup()["tools"] == tool_names


def test_mcp_quality_timeline(tmp_path, monkeypatch):
    from backend import config
    from backend.projects import create_project

    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    created = create_project("Mcp Saga")
    project_dir = tmp_path / created["name"]
    project_dir.mkdir(parents=True, exist_ok=True)
    tool_call(
        "prose_analyze_manuscript",
        {
            "documents": [
                {"name": "one.md", "text": "Mara checked the ledger twice."},
                {"name": "two.md", "text": "Mara checked the ledger again."},
            ],
            "project": "Mcp Saga",
            "persist": True,
        },
    )
    result = tool_call("prose_quality_timeline", {"project": "Mcp Saga"})
    assert result["project"] == "Mcp Saga"
    assert len(result["runs"]) == 1
    assert isinstance(result["runs"][0]["category_counts"], dict)
