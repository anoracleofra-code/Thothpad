from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from backend.story.audits import NarrativeAuditEngine
from backend.story.authority import KnowledgeStatus
from backend.story.branches import branch_comparison, branch_freshness
from backend.story.causality import trace_causality
from backend.story.project import StoryProject
from backend.story.promises import list_promise_items
from backend.story.retcons import retcon_impact
from backend.story.store import StoryStore
from backend.story.threads import list_threads
from backend.story.timeline import query_timeline, who_has_object, world_state_for

_WORD = re.compile(r"[\w'-]{2,}", re.UNICODE)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


@dataclass(slots=True)
class StoryQueryEngine:
    project: StoryProject
    store: StoryStore

    def get_project_understanding(self) -> dict[str, Any]:
        role_counts: dict[str, int] = {}
        for row in self.store.rows(
            """
            SELECT role, COUNT(DISTINCT source_id) AS count
            FROM source_roles WHERE confidence>=0.5 GROUP BY role ORDER BY role
            """
        ):
            role_counts[row["role"]] = row["count"]
        source_count = next(iter(self.store.rows("SELECT COUNT(*) AS count FROM sources WHERE tombstoned=0")))["count"]
        entity_count = next(iter(self.store.rows("SELECT COUNT(*) AS count FROM entities")))["count"]
        conflict_count = next(
            iter(self.store.rows("SELECT COUNT(*) AS count FROM story_conflicts WHERE status='OPEN'"))
        )["count"]
        return {
            "project_id": self.project.project_id,
            "name": self.project.root.name,
            "source_count": source_count,
            "entity_count": entity_count,
            "open_conflict_count": conflict_count,
            "role_counts": role_counts,
            "metadata_location": "application_data" if self.project.metadata_is_external else "project",
        }

    def resolve_entity(self, name: str) -> dict[str, Any]:
        exact = self.store.resolve_entities(name.strip())
        if exact:
            return {"query": name, "matches": [_row_dict(row) for row in exact], "resolution": "exact"}

        pattern = f"%{name.strip().casefold()}%"
        fuzzy = list(
            self.store.rows(
                """
                SELECT e.*, a.alias, a.confidence AS alias_confidence, a.user_confirmed
                FROM entity_aliases a JOIN entities e ON e.entity_id=a.entity_id
                WHERE lower(a.alias) LIKE ? ORDER BY a.user_confirmed DESC,a.confidence DESC LIMIT 12
                """,
                (pattern,),
            )
        )
        return {"query": name, "matches": [_row_dict(row) for row in fuzzy], "resolution": "fuzzy" if fuzzy else "none"}

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        entity = self.store.entity(entity_id)
        if entity is None:
            raise KeyError("entity not found")
        aliases = [
            dict(row)
            for row in self.store.rows(
                """
                SELECT alias,confidence,user_confirmed
                FROM entity_aliases
                WHERE entity_id=?
                ORDER BY user_confirmed DESC,confidence DESC
                """,
                (entity_id,),
            )
        ]
        claims: list[dict[str, Any]] = []
        for row in self.store.query_claims(subject_entity_id=entity_id):
            record = dict(row)
            record["literal_value"] = StoryStore.decode_json(record.pop("literal_value_json"), None)
            record["qualifiers"] = StoryStore.decode_json(record.pop("qualifiers_json"), {})
            grounded = self.store.claim_with_evidence(row["claim_id"])
            record["evidence"] = grounded["evidence"] if grounded else []
            claims.append(record)
        mentions = [
            dict(row)
            for row in self.store.rows(
                """
                SELECT m.source_id,m.start_offset,m.end_offset,m.surface,m.confidence,s.relative_path
                FROM entity_mentions m JOIN sources s ON s.source_id=m.source_id
                WHERE m.entity_id=? AND s.tombstoned=0
                ORDER BY m.confidence DESC,s.relative_path,m.start_offset LIMIT 100
                """,
                (entity_id,),
            )
        ]
        return {"entity": dict(entity), "aliases": aliases, "claims": claims, "mentions": mentions}

    def find_story_evidence(self, query: str, *, limit: int = 16) -> list[dict[str, Any]]:
        terms = []
        seen: set[str] = set()
        for term in _WORD.findall(query.casefold()):
            if term not in seen:
                terms.append(term)
                seen.add(term)
        results = []
        for row in self.store.search_chunks(terms, limit=limit):
            source_roles = [dict(role) for role in self.store.source_roles(row["source_id"])]
            results.append(
                {
                    "source_id": row["source_id"],
                    "path": row["relative_path"],
                    "chunk_id": row["chunk_id"],
                    "heading": row["heading"],
                    "start_offset": row["start_offset"],
                    "end_offset": row["end_offset"],
                    "text": row["text"],
                    "authority": row["authority_default"],
                    "roles": source_roles,
                }
            )
        return results

    def query_claims(
        self,
        *,
        entity: str | None = None,
        predicate: str | None = None,
        branch_id: str = "mainline",
        include_noncanonical: bool = True,
    ) -> list[dict[str, Any]]:
        entity_id: str | None = None
        if entity:
            resolved = self.resolve_entity(entity)
            if not resolved["matches"]:
                return []
            entity_id = resolved["matches"][0]["entity_id"]
        result: list[dict[str, Any]] = []
        for row in self.store.query_claims(
            subject_entity_id=entity_id,
            predicate=predicate,
            branch_id=branch_id,
            include_noncanonical=include_noncanonical,
        ):
            grounded = self.store.claim_with_evidence(row["claim_id"])
            record = dict(row)
            record["literal_value"] = StoryStore.decode_json(record.pop("literal_value_json"), None)
            record["qualifiers"] = StoryStore.decode_json(record.pop("qualifiers_json"), {})
            record["evidence"] = grounded["evidence"] if grounded else []
            result.append(record)
        return result

    def get_story_unit(self, story_unit_id: str) -> dict[str, Any]:
        row = next(iter(self.store.rows("SELECT * FROM story_units WHERE story_unit_id=?", (story_unit_id,))), None)
        if row is None:
            raise KeyError("story unit not found")
        record = dict(row)
        record["metadata"] = StoryStore.decode_json(record.pop("metadata_json"), {})
        return record

    def list_story_units(self, *, source_id: str | None = None, branch_id: str = "mainline") -> list[dict[str, Any]]:
        if source_id:
            rows = self.store.rows(
                "SELECT * FROM story_units WHERE branch_id=? AND source_id=? ORDER BY ordinal",
                (branch_id, source_id),
            )
            return [dict(row) for row in rows]

        records = [
            dict(row)
            for row in self.store.rows(
                """
                SELECT u.*,s.relative_path
                FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                WHERE u.branch_id=?
                """,
                (branch_id,),
            )
        ]
        order = {path.casefold(): index for index, path in enumerate(self.project.active_manuscripts())}
        fallback = len(order) + 1
        records.sort(
            key=lambda record: (
                order.get(str(record.get("relative_path") or "").casefold(), fallback),
                str(record.get("relative_path") or "").casefold(),
                int(record.get("ordinal") or 0),
            )
        )
        return records

    def resolve_story_unit(
        self,
        *,
        source_path: str,
        title: str = "",
        branch_id: str = "mainline",
    ) -> dict[str, Any]:
        """Resolve the native editor scope to a stable Story Unit.

        The project-relative source path identifies the file. The scope title is
        only a within-file disambiguator; folder names never become semantic
        authority.
        """

        normalized = source_path.replace("\\", "/").strip()
        source = self.store.source_by_path(normalized)
        if source is None:
            return {"match": None, "ambiguous": False, "reason": "source_not_indexed"}
        units = self.list_story_units(source_id=str(source["source_id"]), branch_id=branch_id)
        if not units:
            return {"match": None, "ambiguous": False, "reason": "source_has_no_story_units"}
        wanted = title.strip().casefold()
        candidates = [
            item
            for item in units
            if wanted and str(item.get("display_title") or "").strip().casefold() == wanted
        ]
        if not candidates and wanted:
            candidates = [
                item
                for item in units
                if wanted in str(item.get("display_title") or "").strip().casefold()
            ]
        if not candidates and wanted:
            return {"match": None, "ambiguous": False, "reason": "scope_title_not_found"}
        if not candidates and len(units) == 1:
            candidates = units
        if not candidates:
            return {
                "match": None,
                "ambiguous": True,
                "reason": "scope_required_for_multi_unit_source",
                "candidate_count": len(units),
            }
        match = dict(candidates[0])
        match["relative_path"] = normalized
        return {
            "match": match,
            "ambiguous": len(candidates) > 1,
            "candidate_count": len(candidates),
        }

    def list_entities(self, *, limit: int = 200) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 500))
        entities: list[dict[str, Any]] = []
        for row in self.store.rows(
            "SELECT * FROM entities ORDER BY canonical_name COLLATE NOCASE,entity_id LIMIT ?",
            (maximum,),
        ):
            record = dict(row)
            record["metadata"] = StoryStore.decode_json(record.pop("metadata_json", None), {})
            record["aliases"] = [
                dict(alias)
                for alias in self.store.rows(
                    """
                    SELECT alias,confidence,user_confirmed
                    FROM entity_aliases WHERE entity_id=?
                    ORDER BY user_confirmed DESC,confidence DESC,alias COLLATE NOCASE
                    """,
                    (record["entity_id"],),
                )
            ]
            entities.append(record)
        return entities

    def list_conflicts(self, *, include_closed: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 500))
        where = "" if include_closed else " WHERE status='OPEN'"
        conflicts: list[dict[str, Any]] = []
        for row in self.store.rows(
            f"SELECT * FROM story_conflicts{where} ORDER BY severity DESC,conflict_id LIMIT ?",  # noqa: S608 - fixed clause only
            (maximum,),
        ):
            record = dict(row)
            record["metadata"] = StoryStore.decode_json(record.pop("metadata_json", None), {})
            claim_ids = [
                str(item["claim_id"])
                for item in self.store.rows(
                    "SELECT claim_id FROM conflict_claims WHERE conflict_id=? ORDER BY claim_id",
                    (record["conflict_id"],),
                )
            ]
            record["claims"] = [
                grounded
                for claim_id in claim_ids
                if (grounded := self._grounded_claim_summary(claim_id)) is not None
            ]
            conflicts.append(record)
        return conflicts

    def character_knowledge(
        self,
        character: str,
        *,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved = self.resolve_entity(character)
        if not resolved["matches"]:
            return []
        entity_id = resolved["matches"][0]["entity_id"]
        rows = self.store.rows(
            """
            SELECT k.*,c.predicate,c.literal_value_json,c.status AS claim_status
            FROM knowledge_state k JOIN claims c ON c.claim_id=k.claim_id
            WHERE k.character_id=? AND k.branch_id=?
              AND c.status NOT IN ('SUPERSEDED','ARCHIVED','OPEN')
            ORDER BY k.acquired_at,k.knowledge_id
            """,
            (entity_id, branch_id),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["literal_value"] = StoryStore.decode_json(item.pop("literal_value_json"), None)
            result.append(item)
            if len(result) >= max(1, min(int(limit), 500)):
                break
        return result

    def character_beliefs(
        self,
        character: str,
        *,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        belief_states = {
            KnowledgeStatus.BELIEVES.value,
            KnowledgeStatus.SUSPECTS.value,
            KnowledgeStatus.DISBELIEVES.value,
        }
        return [
            item
            for item in self.character_knowledge(character, branch_id=branch_id, limit=limit)
            if item["state"] in belief_states
        ]

    def timeline(self, *, branch_id: str = "mainline", limit: int = 200) -> list[dict[str, Any]]:
        return query_timeline(self.store, branch_id=branch_id)[: max(1, min(int(limit), 500))]

    def world_state(
        self,
        entity: str,
        *,
        state_type: str | None = None,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved = self.resolve_entity(entity)
        if not resolved["matches"]:
            return []
        entity_id = resolved["matches"][0]["entity_id"]
        return world_state_for(
            self.store,
            entity_id,
            state_type=state_type,
            branch_id=branch_id,
        )[: max(1, min(int(limit), 500))]

    def possession_state(
        self,
        object_name: str,
        *,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved = self.resolve_entity(object_name)
        if not resolved["matches"]:
            return []
        object_entity_id = resolved["matches"][0]["entity_id"]
        return who_has_object(self.store, object_entity_id, branch_id=branch_id)[: max(1, min(int(limit), 500))]

    def reader_state(
        self,
        *,
        branch_id: str = "mainline",
        through_story_unit: str | None = None,
    ) -> list[dict[str, Any]]:
        records = [
            dict(row)
            for row in self.store.rows(
                """
                SELECT r.*,c.predicate,c.literal_value_json,u.display_title,
                       u.source_id AS unit_source_id,u.ordinal AS unit_ordinal,
                       s.relative_path AS unit_source_path
                FROM reader_state r
                LEFT JOIN claims c ON c.claim_id=r.claim_id
                LEFT JOIN story_units u ON u.story_unit_id=r.story_unit_id
                LEFT JOIN sources s ON s.source_id=u.source_id
                WHERE r.branch_id=?
                  AND (r.claim_id IS NULL OR c.status NOT IN ('SUPERSEDED','ARCHIVED','OPEN'))
                """,
                (branch_id,),
            )
        ]
        if not through_story_unit:
            return records

        target = next(
            iter(
                self.store.rows(
                    """
                    SELECT u.source_id,u.ordinal,s.relative_path
                    FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                    WHERE u.story_unit_id=? AND u.branch_id=?
                    """,
                    (through_story_unit, branch_id),
                )
            ),
            None,
        )
        if target is None:
            return []

        order = {path.casefold(): index for index, path in enumerate(self.project.active_manuscripts())}
        target_path = str(target["relative_path"] or "")
        target_position = order.get(target_path.casefold())
        visible: list[dict[str, Any]] = []
        for record in records:
            # A null story_unit_id denotes reader state that is not introduced
            # at a later manuscript position (for example, a premise known at
            # page one), so it remains visible through every cutoff.
            if not record.get("story_unit_id"):
                visible.append(record)
                continue
            if record.get("unit_source_id") == target["source_id"]:
                if int(record.get("unit_ordinal") or 0) <= int(target["ordinal"]):
                    visible.append(record)
                continue
            candidate_path = str(record.get("unit_source_path") or "")
            candidate_position = order.get(candidate_path.casefold())
            if target_position is not None and candidate_position is not None and candidate_position < target_position:
                visible.append(record)
        return visible

    def _evidence_quote(self, evidence: dict[str, Any]) -> str | None:
        start = int(evidence.get("start_offset", 0))
        end = int(evidence.get("end_offset", start))
        if end <= start:
            return None
        row = next(
            iter(
                self.store.rows(
                    """
                    SELECT start_offset,end_offset,text
                    FROM source_chunks
                    WHERE source_id=? AND start_offset<=? AND end_offset>=?
                    ORDER BY (end_offset-start_offset),ordinal LIMIT 1
                    """,
                    (evidence.get("source_id"), start, end),
                )
            ),
            None,
        )
        if row is None:
            return None
        local_start = start - int(row["start_offset"])
        local_end = end - int(row["start_offset"])
        quote = str(row["text"])[local_start:local_end]
        expected = str(evidence.get("quote_hash") or "")
        if expected and hashlib.sha256(quote.encode("utf-8")).hexdigest() != expected:
            return None
        return quote

    def _grounded_claim_summary(self, claim_id: str | None) -> dict[str, Any] | None:
        if not claim_id:
            return None
        grounded = self.store.claim_with_evidence(claim_id)
        if grounded is None:
            return None
        claim = dict(grounded["claim"])
        claim["literal_value"] = StoryStore.decode_json(claim.pop("literal_value_json"), None)
        claim["qualifiers"] = StoryStore.decode_json(claim.pop("qualifiers_json"), {})
        evidence_rows: list[dict[str, Any]] = []
        for raw in grounded["evidence"]:
            evidence = dict(raw)
            quote = self._evidence_quote(evidence)
            if quote is not None:
                evidence["quote"] = quote
            evidence_rows.append(evidence)
        claim["evidence"] = evidence_rows
        return claim

    def threads(self, *, branch_id: str = "mainline", limit: int = 200) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in list_threads(self.store, branch_id=branch_id):
            evidence_claim_id = str(item.get("metadata", {}).get("evidence_claim_id") or "") or None
            item["evidence_claim"] = self._grounded_claim_summary(evidence_claim_id)
            result.append(item)
            if len(result) >= max(1, min(int(limit), 500)):
                break
        return result

    def promise_items(
        self,
        *,
        item_type: str | None = None,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in list_promise_items(self.store, item_type=item_type, branch_id=branch_id):
            item["evidence_claim"] = self._grounded_claim_summary(item.get("evidence_claim_id"))
            result.append(item)
            if len(result) >= max(1, min(int(limit), 500)):
                break
        return result

    def reader_questions(self, *, branch_id: str = "mainline", limit: int = 200) -> list[dict[str, Any]]:
        return self.promise_items(item_type="READER_QUESTION", branch_id=branch_id, limit=limit)

    def dramatic_promises(self, *, branch_id: str = "mainline", limit: int = 200) -> list[dict[str, Any]]:
        return self.promise_items(item_type="DRAMATIC_PROMISE", branch_id=branch_id, limit=limit)

    def causality(
        self,
        *,
        record_kind: str,
        record_id: str,
        branch_id: str = "mainline",
        direction: str = "both",
        maximum_depth: int = 8,
    ) -> dict[str, Any]:
        result = trace_causality(
            self.store,
            record_kind=record_kind,
            record_id=record_id,
            branch_id=branch_id,
            direction=direction,
            maximum_depth=maximum_depth,
        )
        for edge in result["edges"]:
            edge["evidence_claim"] = self._grounded_claim_summary(edge.get("evidence_claim_id"))
        return result

    def decision_history(
        self,
        *,
        character: str | None = None,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        entity_id: str | None = None
        if character:
            resolved = self.resolve_entity(character)
            if not resolved["matches"]:
                return []
            entity_id = str(resolved["matches"][0]["entity_id"])
        params: list[Any] = [branch_id]
        character_filter = ""
        if entity_id:
            character_filter = " AND d.agent_entity_id=?"
            params.append(entity_id)
        params.append(max(1, min(int(limit), 500)))
        result: list[dict[str, Any]] = []
        for row in self.store.rows(
            f"""
            SELECT d.*,e.canonical_name AS agent_name,u.display_title AS story_unit_title
            FROM decisions d
            LEFT JOIN entities e ON e.entity_id=d.agent_entity_id
            LEFT JOIN story_units u ON u.story_unit_id=d.story_unit_id
            WHERE d.branch_id=?{character_filter}
            ORDER BY d.rowid LIMIT ?
            """,  # noqa: S608 - optional filter is fixed SQL
            params,
        ):
            item = dict(row)
            item["metadata"] = StoryStore.decode_json(item.pop("metadata_json"), {})
            result.append(item)
        return result

    def opposition_state(
        self,
        *,
        objective_id: str | None = None,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [branch_id]
        objective_filter = ""
        if objective_id:
            objective_filter = " AND o.objective_id=?"
            params.append(objective_id)
        params.append(max(1, min(int(limit), 500)))
        result: list[dict[str, Any]] = []
        for row in self.store.rows(
            f"""
            SELECT o.*,e.canonical_name AS source_entity_name,u.display_title AS story_unit_title
            FROM opposition_state o
            LEFT JOIN entities e ON e.entity_id=o.source_entity_id
            LEFT JOIN story_units u ON u.story_unit_id=o.story_unit_id
            WHERE o.branch_id=?{objective_filter}
            ORDER BY o.rowid LIMIT ?
            """,  # noqa: S608 - optional filter is fixed SQL
            params,
        ):
            item = dict(row)
            item["metadata"] = StoryStore.decode_json(item.pop("metadata_json"), {})
            result.append(item)
        return result

    def scene_contract(self, story_unit_id: str) -> dict[str, Any]:
        row = next(
            iter(
                self.store.rows(
                    """
                    SELECT c.*,u.display_title,u.kind
                    FROM scene_contracts c JOIN story_units u ON u.story_unit_id=c.story_unit_id
                    WHERE c.story_unit_id=? AND u.branch_id='mainline'
                    """,
                    (story_unit_id,),
                )
            ),
            None,
        )
        if row is None:
            return {}
        item = dict(row)
        item["contract"] = StoryStore.decode_json(item.pop("contract_json"), {})
        return item

    def author_decisions(
        self,
        *,
        story_unit_id: str | None = None,
        branch_id: str = "mainline",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [branch_id]
        story_filter = ""
        if story_unit_id:
            story_filter = " AND a.story_unit_id=?"
            params.append(story_unit_id)
        params.append(max(1, min(int(limit), 500)))
        return [
            dict(row)
            for row in self.store.rows(
                f"""
                SELECT a.*,u.display_title AS story_unit_title
                FROM author_decisions a
                LEFT JOIN story_units u ON u.story_unit_id=a.story_unit_id
                WHERE a.branch_id=?{story_filter}
                ORDER BY a.rowid LIMIT ?
                """,  # noqa: S608 - optional filter is fixed SQL
                params,
            )
        ]

    def audit_story_unit(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        return NarrativeAuditEngine(self.project, self.store).audit_story_unit(
            story_unit_id,
            branch_id=branch_id,
        )

    def audit_scene(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        result = self.audit_story_unit(story_unit_id, branch_id=branch_id)
        if result["story_unit"]["kind"] != "scene":
            raise ValueError("audit_scene requires a scene story unit")
        result["audit_kind"] = "scene"
        return result

    def audit_chapter(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        result = self.audit_story_unit(story_unit_id, branch_id=branch_id)
        if result["story_unit"]["kind"] not in {"chapter", "manuscript"}:
            raise ValueError("audit_chapter requires a chapter or manuscript story unit")
        result["audit_kind"] = "chapter"
        return result

    def branches(self, *, limit: int = 200) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.store.rows(
            "SELECT * FROM branches WHERE branch_id<>'mainline' ORDER BY rowid LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ):
            item = dict(row)
            item["assumptions"] = StoryStore.decode_json(item.pop("assumptions_json"), [])
            item["freshness"] = branch_freshness(self.store, item["branch_id"])
            item["overlay_count"] = next(
                iter(
                    self.store.rows(
                        "SELECT COUNT(*) AS count FROM branch_overlays WHERE branch_id=?",
                        (item["branch_id"],),
                    )
                )
            )["count"]
            item["merged_overlay_count"] = next(
                iter(
                    self.store.rows(
                        "SELECT COUNT(*) AS count FROM branch_merge_history WHERE branch_id=?",
                        (item["branch_id"],),
                    )
                )
            )["count"]
            result.append(item)
        return result

    def compare_branch(self, branch_id: str) -> dict[str, Any]:
        return branch_comparison(self.store, branch_id)

    def retcon_impact(
        self,
        *,
        source_kind: str,
        source_id: str,
        maximum_nodes: int = 5_000,
    ) -> dict[str, Any]:
        return retcon_impact(
            self.store,
            source_kind=source_kind,
            source_id=source_id,
            maximum_nodes=max(1, min(int(maximum_nodes), 5_000)),
        )

    def cold_reader_at(
        self,
        story_unit_id: str,
        *,
        prompt: str = "What can a reader know, wonder, and reasonably expect here?",
        branch_id: str = "mainline",
        maximum_chars: int = 40_000,
    ) -> dict[str, Any]:
        from backend.story.reader_intelligence import ReaderIntelligence

        return ReaderIntelligence(self.project, self.store).cold_reader_at(
            story_unit_id,
            prompt=prompt,
            branch_id=branch_id,
            maximum_chars=maximum_chars,
        )

    def reveal_fairness(
        self,
        story_unit_id: str,
        *,
        claim_id: str | None = None,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        from backend.story.reader_intelligence import ReaderIntelligence

        return ReaderIntelligence(self.project, self.store).reveal_fairness(
            story_unit_id,
            claim_id=claim_id,
            branch_id=branch_id,
            limit=limit,
        )

    def reader_expectations(
        self,
        story_unit_id: str,
        *,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        from backend.story.reader_intelligence import ReaderIntelligence

        return ReaderIntelligence(self.project, self.store).reader_expectations_at(
            story_unit_id,
            branch_id=branch_id,
            limit=limit,
        )

    def dramatic_irony(
        self,
        story_unit_id: str,
        *,
        character: str,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        from backend.story.reader_intelligence import ReaderIntelligence

        return ReaderIntelligence(self.project, self.store).dramatic_irony_at(
            story_unit_id,
            character=character,
            branch_id=branch_id,
            limit=limit,
        )

    def writer_model(
        self,
        *,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        include_ignored: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        from backend.story.writer_model import writer_model

        return writer_model(
            self.store,
            scope_kind=scope_kind,
            scope_id=scope_id,
            include_ignored=include_ignored,
            limit=limit,
        )

    def explain_writer_preference(self, preference_id: str) -> dict[str, Any]:
        from backend.story.writer_model import explain_preference

        return explain_preference(self.store, preference_id)

    def editorial_council(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        from backend.story.editorial_council import EditorialCouncil

        return EditorialCouncil(self.project, self.store).run(story_unit_id, branch_id=branch_id)

    def story_lenses(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        from backend.story.lenses import list_story_lenses

        return list_story_lenses(self.store, include_archived=include_archived)

    def story_lens(self, lens_id: str) -> dict[str, Any]:
        from backend.story.lenses import story_lens

        return story_lens(self.store, lens_id)

    def run_story_lens(self, lens_id: str, *, maximum_findings: int = 100) -> dict[str, Any]:
        from backend.story.lenses import run_story_lens

        return run_story_lens(self.store, lens_id, maximum_findings=maximum_findings)

    def reader_experience(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        from backend.story.reader_experience import analyze_story_unit_experience

        return analyze_story_unit_experience(self.store, story_unit_id, branch_id=branch_id)

    def reader_experience_timeline(
        self,
        *,
        branch_id: str = "mainline",
        source_id: str | None = None,
        maximum_units: int = 500,
    ) -> dict[str, Any]:
        from backend.story.reader_experience import reader_experience_timeline

        return reader_experience_timeline(
            self.project,
            self.store,
            branch_id=branch_id,
            source_id=source_id,
            maximum_units=maximum_units,
        )

    def project_sources(self, *, role: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.sources_for_role(role, minimum_confidence=0.0) if role else self.store.list_sources()
        return [dict(row) for row in rows]

    def export_debug_snapshot(self) -> str:
        """Human-readable inspection only; never used as an authority source."""

        snapshot = {
            "understanding": self.get_project_understanding(),
            "sources": self.project_sources(),
            "entities": [dict(row) for row in self.store.list_entities()],
            "units": self.list_story_units(),
        }
        return json.dumps(snapshot, ensure_ascii=False, indent=2)


R0_QUERY_NAMES = frozenset(
    {
        "get_project_understanding",
        "get_story_context",
        "get_story_unit",
        "list_story_units",
        "resolve_entity",
        "get_entity",
        "find_story_evidence",
        "query_claims",
        "query_timeline",
        "get_world_state",
        "where_is_entity",
        "who_has_object",
        "get_character_knowledge",
        "get_character_beliefs",
        "get_reader_state",
        "list_threads",
        "list_reader_questions",
        "list_dramatic_promises",
        "trace_causality",
        "get_decision_history",
        "get_opposition_state",
        "get_scene_contract",
        "get_author_decisions",
        "audit_scene",
        "audit_chapter",
        "list_branches",
        "compare_branch",
        "get_retcon_impact",
        "cold_reader_at",
        "audit_reveal_fairness",
        "get_reader_expectations",
        "get_dramatic_irony",
        "get_writer_model",
        "explain_writer_preference",
        "run_editorial_council",
        "list_story_lenses",
        "get_story_lens",
        "run_story_lens",
        "get_reader_experience",
        "get_reader_experience_timeline",
    }
)
