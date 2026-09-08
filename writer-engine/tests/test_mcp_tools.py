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
    assert len(tool_names) == 78

    readme = Path(__file__).resolve().parents[1] / "README.md"
    documented = [
        line[2:].strip().strip("`")
        for line in readme.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `prose_") or line.startswith("- `story_")
    ]
    assert documented == tool_names
    assert agent_setup()["tools"] == tool_names


def test_story_mcp_requires_writer_initialized_project(tmp_path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text('# Chapter One\nMara said, "No."', encoding="utf-8")
    with pytest.raises(PermissionError, match="not been initialized"):
        tool_call("story_project_understanding", {"project_root": str(root)})


def test_story_mcp_reads_initialized_project_without_mutation(tmp_path):
    from backend.story.ingest import ProjectIngestor
    from backend.story.project import StoryProject
    from backend.story.store import StoryStore

    root = tmp_path / "book"
    root.mkdir()
    (root / "Mara.md").write_text(
        "# Mara\nAppearance: scarred hand\nGoal: leave the city\nRelationships: trusts Iven\n",
        encoding="utf-8",
    )
    project = StoryProject.open(root)
    with StoryStore(project.cache_path) as store:
        ProjectIngestor(project, store).ingest()

    understanding = tool_call("story_project_understanding", {"project_root": str(root)})
    assert understanding["source_count"] == 1
    resolved = tool_call("story_resolve_entity", {"project_root": str(root), "name": "Mara"})
    assert resolved["matches"]
    claims = tool_call("story_query_claims", {"project_root": str(root), "entity": "Mara"})
    assert {row["predicate"] for row in claims["claims"]} >= {"appearance", "goal"}


def test_story_mcp_exposes_typed_timeline_world_and_epistemic_state(tmp_path):
    from backend.story.authority import AuthorityStatus, KnowledgeStatus
    from backend.story.claims import create_claim
    from backend.story.ingest import ProjectIngestor
    from backend.story.knowledge import set_character_knowledge
    from backend.story.project import StoryProject
    from backend.story.query import StoryQueryEngine
    from backend.story.store import StoryStore
    from backend.story.timeline import add_timeline_event, set_world_state

    root = tmp_path / "book"
    root.mkdir()
    (root / "Mara profile.md").write_text(
        "# Mara\nAppearance: scarred hand\nGoal: protect the bell\nRelationships: trusts Iven\n",
        encoding="utf-8",
    )
    project = StoryProject.open(root)
    with StoryStore(project.cache_path) as store:
        ProjectIngestor(project, store).ingest()
        query = StoryQueryEngine(project, store)
        mara_id = query.resolve_entity("Mara")["matches"][0]["entity_id"]
        bell_id = "test-bell-entity"
        store.upsert_entity(bell_id, "Silver Bell", "object", status=AuthorityStatus.CONFIRMED_CANON)
        claim_id = create_claim(
            store,
            project_id=project.project_id,
            subject_entity_id=mara_id,
            predicate="bell_is_cursed",
            literal_value=True,
            status=AuthorityStatus.CONFIRMED_CANON,
            created_by="writer",
            stable_key="mara-bell-cursed",
        )
        set_character_knowledge(
            store,
            character_id=mara_id,
            claim_id=claim_id,
            state=KnowledgeStatus.SUSPECTS,
        )
        add_timeline_event(
            store,
            title="Mara enters Harbor City",
            time_start="DAY-01",
            precision="EXACT",
            status=AuthorityStatus.CONFIRMED_CANON,
        )
        set_world_state(
            store,
            entity_id=mara_id,
            state_type="location",
            value="Harbor City",
            status=AuthorityStatus.CONFIRMED_CANON,
        )
        set_world_state(
            store,
            entity_id=mara_id,
            state_type="possession",
            value=bell_id,
            status=AuthorityStatus.CONFIRMED_CANON,
        )
        store.commit()

    beliefs = tool_call(
        "story_get_character_beliefs",
        {"project_root": str(root), "character": "Mara"},
    )
    assert beliefs["beliefs"][0]["state"] == KnowledgeStatus.SUSPECTS

    timeline = tool_call("story_query_timeline", {"project_root": str(root)})
    assert timeline["events"][0]["title"] == "Mara enters Harbor City"

    world = tool_call(
        "story_get_world_state",
        {"project_root": str(root), "entity": "Mara"},
    )
    assert {item["state_type"] for item in world["world_state"]} >= {"location", "possession"}

    location = tool_call(
        "story_where_is_entity",
        {"project_root": str(root), "entity": "Mara"},
    )
    assert location["locations"][0]["value"] == "Harbor City"

    possession = tool_call(
        "story_who_has_object",
        {"project_root": str(root), "object": "Silver Bell"},
    )
    assert possession["possessions"][0]["holder_name"] == "Mara"

    context = tool_call(
        "story_get_context",
        {
            "project_root": str(root),
            "prompt": "What does Mara believe about the bell?",
            "active_character": "Mara",
        },
    )
    assert "story_state" in context
    assert context["story_state"]["character_knowledge"]


def test_story_mcp_exposes_grounded_narrative_architecture(tmp_path):
    from backend.story.authority import AuthorityStatus
    from backend.story.causality import add_causal_edge, add_decision, add_opposition
    from backend.story.ingest import ProjectIngestor
    from backend.story.persistence import persist_writer_state
    from backend.story.project import StoryProject
    from backend.story.query import StoryQueryEngine
    from backend.story.store import StoryStore

    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara rings the bell.\n", encoding="utf-8")
    (root / "outline.md").write_text(
        "# Outline\n"
        "Dramatic Promise: the bell will ring before dawn\n"
        "Reader Question: who moved the bell?\n"
        "Plot Thread: Mara investigates the bell\n",
        encoding="utf-8",
    )
    project = StoryProject.open(root)
    with StoryStore(project.cache_path) as store:
        ProjectIngestor(project, store).ingest()
        query = StoryQueryEngine(project, store)
        chapter = store.source_by_path("chapter.md")
        assert chapter is not None
        unit_id = query.list_story_units(source_id=chapter["source_id"])[0]["story_unit_id"]
        mara_id = "mcp-mara"
        store.upsert_entity(mara_id, "Mara", "character", status=AuthorityStatus.CONFIRMED_CANON)
        decision_id = add_decision(
            store,
            description="Mara rings the bell",
            agent_entity_id=mara_id,
            story_unit_id=unit_id,
            status=AuthorityStatus.AUTHOR_LOCKED,
        )
        add_causal_edge(
            store,
            cause_kind="decision",
            cause_id=decision_id,
            effect_kind="event",
            effect_id="bell-rings",
        )
        add_opposition(
            store,
            objective_id="ring-bell",
            description="The guard blocks the tower",
            source_entity_id=mara_id,
            story_unit_id=unit_id,
        )
        store.connection.execute(
            "INSERT INTO scene_contracts(story_unit_id,contract_json,status,updated_by) VALUES(?,?,?,?)",
            (unit_id, '{"purpose":"force a choice"}', "AUTHOR_LOCKED", "writer"),
        )
        store.connection.execute(
            """
            INSERT INTO author_decisions(
                author_decision_id,title,decision,rationale,story_unit_id,branch_id,status
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "mcp-author-choice",
                "Mara owns the choice",
                "Mara rings the bell herself",
                "Preserve agency",
                unit_id,
                "mainline",
                "AUTHOR_LOCKED",
            ),
        )
        # This test creates writer-owned state with low-level primitives to
        # exercise the query surface. Under the Universal Story Engine cache
        # contract, writer-owned rows must be projected into durable state
        # before a later Story Project ingest is allowed to rebuild the cache.
        persist_writer_state(project, store)
        store.commit()

    threads = tool_call("story_list_threads", {"project_root": str(root), "limit": 1})
    assert threads["threads"][0]["evidence_claim"]["evidence"][0]["stale"] == 0
    questions = tool_call("story_list_reader_questions", {"project_root": str(root)})
    assert questions["reader_questions"][0]["title"] == "who moved the bell?"
    promises = tool_call("story_list_dramatic_promises", {"project_root": str(root)})
    assert promises["dramatic_promises"][0]["title"] == "the bell will ring before dawn"
    causal = tool_call(
        "story_trace_causality",
        {"project_root": str(root), "record_kind": "decision", "record_id": decision_id},
    )
    assert {node["id"] for node in causal["causality"]["nodes"]} == {decision_id, "bell-rings"}
    decisions = tool_call("story_get_decision_history", {"project_root": str(root), "character": "Mara"})
    assert decisions["decisions"][0]["decision_id"] == decision_id
    opposition = tool_call(
        "story_get_opposition_state",
        {"project_root": str(root), "objective_id": "ring-bell"},
    )
    assert opposition["opposition"][0]["description"] == "The guard blocks the tower"
    contract = tool_call(
        "story_get_scene_contract",
        {"project_root": str(root), "story_unit_id": unit_id},
    )
    assert contract["scene_contract"]["contract"]["purpose"] == "force a choice"
    author = tool_call(
        "story_get_author_decisions",
        {"project_root": str(root), "story_unit_id": unit_id},
    )
    assert author["author_decisions"][0]["author_decision_id"] == "mcp-author-choice"

    audit = tool_call(
        "story_audit_chapter",
        {"project_root": str(root), "story_unit_id": unit_id},
    )["audit"]
    assert audit["diagnostic_only"] is True
    assert audit["absence_means_untracked"] is True
    assert audit["coverage"]["decisions"] == 1
    assert audit["coverage"]["causal_edges"] == 1
    assert audit["coverage"]["opposition_records"] == 1
    assert audit["coverage"]["scene_contracts"] == 1
    assert audit["coverage"]["author_decisions"] == 1


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
