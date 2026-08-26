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
