from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.context import CompiledContext, ContextCompiler, EpistemicMode
from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class EgressInspector:
    project: StoryProject
    store: StoryStore

    def preview(
        self,
        *,
        prompt: str,
        mode: EpistemicMode | str = EpistemicMode.AUTHOR_OMNISCIENT,
        branch_id: str = "mainline",
        active_story_unit: str | None = None,
        active_source_path: str = "",
        active_document_end: int | None = None,
        active_character: str = "",
        maximum_chars: int = 40_000,
        selected_is_remote: bool = False,
        include_text_preview: bool = False,
    ) -> dict[str, Any]:
        compiled = ContextCompiler(self.project, self.store).compile(
            prompt=prompt,
            mode=mode,
            branch_id=branch_id,
            active_story_unit=active_story_unit,
            active_source_path=active_source_path,
            active_document_end=active_document_end,
            active_character=active_character,
            maximum_chars=maximum_chars,
        )
        return self.from_compiled(
            compiled,
            selected_is_remote=selected_is_remote,
            include_text_preview=include_text_preview,
        )

    @staticmethod
    def from_compiled(
        compiled: CompiledContext,
        *,
        selected_is_remote: bool,
        include_text_preview: bool = False,
    ) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        preview_budget = 20_000
        preview_used = 0
        for item in compiled.items:
            record: dict[str, Any] = {
                "path": item.path,
                "heading": item.heading,
                "characters": len(item.text),
                "authority": item.authority,
                "roles": list(item.roles),
                "reason": item.reason,
            }
            if include_text_preview and preview_used < preview_budget:
                text = item.text[: max(0, preview_budget - preview_used)]
                record["text_preview"] = text
                preview_used += len(text)
            sources.append(record)
        state = compiled.model_state()
        return {
            "would_leave_machine": bool(selected_is_remote),
            "selected_is_remote": bool(selected_is_remote),
            "project_content_is_data": True,
            "credentials_included": False,
            "absolute_paths_included": False,
            "source_count": len(sources),
            "source_characters": sum(int(item["characters"]) for item in sources),
            "sources": sources,
            "story_state_counts": {
                "objective_claims": len(state.get("objective_claims", [])),
                "character_knowledge": len(state.get("character_knowledge", [])),
                "reader_state": len(state.get("reader_state", [])),
                "branch_overlays": len(state.get("branch_overlays", [])),
            },
            "epistemic_boundary": dict(compiled.epistemic_boundary),
            "budget": {"used_chars": compiled.used_chars, "maximum_chars": compiled.maximum_chars},
            "text_preview_included": bool(include_text_preview),
            "text_preview_characters": preview_used,
        }
