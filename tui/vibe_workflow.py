import copy
import re
from typing import Any

from textual.widgets import ContentSwitcher

from core.audio_analysis import FEATURE_PRESENTATION, IMPACT_PRESENTATION
from core.jobs import JobType
from tui.flows.management import ManagementFlow


VIBE_FEATURES: list[tuple[str, str, dict[str, Any]]] = [
    ("tempo_bpm", "Tempo", FEATURE_PRESENTATION["tempo_bpm"]),
    ("tempo_variability", "Tempo stability", FEATURE_PRESENTATION["tempo_variability"]),
    ("onset_density", "Activity", FEATURE_PRESENTATION["onset_density"]),
    ("energy_mean", "Intensity", FEATURE_PRESENTATION["energy_mean"]),
    ("impact", "Dynamics", IMPACT_PRESENTATION),
    ("spectral_brightness", "Tone", FEATURE_PRESENTATION["spectral_brightness"]),
    ("spectral_flatness", "Texture", FEATURE_PRESENTATION["spectral_flatness"]),
    ("instrumentalness", "Vocals", FEATURE_PRESENTATION["instrumentalness"]),
]

VIBE_FEATURE_BY_FIELD = {field: (name, presentation) for field, name, presentation in VIBE_FEATURES}

FEATURE_ALIASES = {
    "tempo": "tempo_bpm",
    "felt tempo": "tempo_bpm",
    "speed": "tempo_bpm",
    "pace": "tempo_bpm",
    "tempo stability": "tempo_variability",
    "stability": "tempo_variability",
    "activity": "onset_density",
    "busy": "onset_density",
    "density": "onset_density",
    "intensity": "energy_mean",
    "energy": "energy_mean",
    "dynamics": "impact",
    "dynamic": "impact",
    "impact": "impact",
    "tone": "spectral_brightness",
    "brightness": "spectral_brightness",
    "texture": "spectral_flatness",
    "noise": "spectral_flatness",
    "vocals": "instrumentalness",
    "vocal": "instrumentalness",
    "vocalness": "instrumentalness",
    "instrumental": "instrumentalness",
    "instrumentalness": "instrumentalness",
}

GENERIC_BUCKETS_5 = {
    "1": 1,
    "low": 1,
    "2": 2,
    "medium low": 2,
    "medium-low": 2,
    "med low": 2,
    "3": 3,
    "medium": 3,
    "mid": 3,
    "4": 4,
    "medium high": 4,
    "medium-high": 4,
    "med high": 4,
    "5": 5,
    "high": 5,
}

GENERIC_BUCKETS_3 = {
    "1": 1,
    "2": 2,
    "3": 3,
}


class VibeWorkflowMixin:
    def empty_vibe_rule(self) -> dict:
        return {
            "version": 1,
            "required": {"match": "all", "rules": []},
            "groups": [{"match": "all", "rules": []}],
        }

    def vibe_presets(self) -> list[tuple[str, dict]]:
        return [
            ("Blank Vibe", self.empty_vibe_rule()),
            ("Studying", self.vibe_rule_from_specs(
                required=[("spectral_flatness", [1, 2]), ("energy_mean", [2, 3])],
                groups=[
                    [("spectral_brightness", [2, 3]), ("onset_density", [2, 3]), ("impact", [1, 2])],
                    [("spectral_brightness", [1, 2]), ("tempo_variability", [1]), ("tempo_bpm", [1, 2])],
                ],
            )),
            ("Driving", self.vibe_rule_from_specs(
                required=[("energy_mean", [3, 4, 5])],
                groups=[
                    [("tempo_bpm", [3, 4, 5]), ("onset_density", [3, 4, 5])],
                    [("impact", [3, 4, 5]), ("spectral_brightness", [3, 4])],
                ],
            )),
            ("Workout", self.vibe_rule_from_specs(
                required=[("energy_mean", [4, 5]), ("onset_density", [4, 5])],
                groups=[
                    [("tempo_bpm", [4, 5])],
                    [("impact", [4, 5])],
                ],
            )),
            ("Sleep", self.vibe_rule_from_specs(
                required=[("energy_mean", [1, 2]), ("onset_density", [1, 2]), ("impact", [1, 2])],
                groups=[
                    [("spectral_brightness", [1, 2]), ("spectral_flatness", [1, 2]), ("tempo_bpm", [1, 2])],
                ],
            )),
            ("Bright Motion", self.vibe_rule_from_specs(
                required=[],
                groups=[
                    [("spectral_brightness", [4, 5]), ("onset_density", [4, 5]), ("tempo_bpm", [3, 4, 5])],
                ],
            )),
            ("Warm Focus", self.vibe_rule_from_specs(
                required=[("spectral_brightness", [2, 3]), ("spectral_flatness", [1, 2])],
                groups=[
                    [("onset_density", [2, 3]), ("energy_mean", [2, 3])],
                ],
            )),
        ]

    def vibe_rule_from_specs(self, *, required: list[tuple[str, list[int]]], groups: list[list[tuple[str, list[int]]]]) -> dict:
        return {
            "version": 1,
            "required": {"match": "all", "rules": [self.vibe_rule_item(field, values) for field, values in required]},
            "groups": [
                {"match": "all", "rules": [self.vibe_rule_item(field, values) for field, values in group]}
                for group in groups
            ] or [{"match": "all", "rules": []}],
        }

    def vibe_rule_item(self, field: str, values: list[int]) -> dict:
        return {"field": field, "op": "bucket_is", "values": sorted(set(values))}

    async def start_create_vibe_flow(self, name: str, rule: dict | None = None) -> None:
        self.current_vibe_collection_id = None
        self.current_vibe_name = name
        self.current_vibe_rule = copy.deepcopy(rule or self.empty_vibe_rule())
        self.query_one(ContentSwitcher).current = "vibe_editor"
        self._nav_stack.append("vibe_editor")
        self.management_flow = None
        self.input_mode = "NORMAL"
        await self.query_one("#vibe_editor").refresh_editor()
        self._focus_main_content("vibe_editor")
        self.update_current_context_actions()
        self.show_status("Status: Ready")

    def vibe_editor_actions(self) -> str:
        if self.input_mode == "VIBE_RULE_FEATURE":
            return self.vibe_feature_hint_actions(self.input_query)
        if self.input_mode == "VIBE_RULE_LEVEL":
            field = self.vibe_pending_rule.get("field") if getattr(self, "vibe_pending_rule", None) else None
            if field:
                return self.vibe_level_hint_actions(field, self.input_query)
        if self.input_mode == "VIBE_RULE_TARGET":
            action = self.vibe_rule_transfer.get("action", "copy") if getattr(self, "vibe_rule_transfer", None) else "copy"
            label = "Move Here" if action == "move" else "Paste Here"
            return f"Enter   {label}\nEsc     Cancel"
        return "A      Add Rule\nG      New Group\nE      Edit Rule\nD      Delete\nL      Loosen\nM      Move Rule\nC      Copy Rule\nP      Preview Matches\nEnter  Save Vibe\nEsc    Cancel"

    def vibe_feature_hint_actions(self, text: str) -> str:
        return "\n".join(self.vibe_rule_hint_tokens(text, None))

    def vibe_level_hint_actions(self, field: str, text: str) -> str:
        return "\n".join(self.vibe_rule_hint_tokens(text, field))

    def vibe_rule_hint_tokens(self, text: str, forced_field: str | None) -> list[str]:
        state = self.vibe_rule_hint_state(text, forced_field)
        field = state.get("field") or forced_field
        if state["kind"] == "feature":
            return [name for _field, name, _presentation in VIBE_FEATURES]
        if state["kind"] == "after_feature":
            tokens = ["is"]
            if state.get("ambiguous_tempo"):
                tokens.append("stability")
            return tokens
        if state["kind"] == "bucket":
            return self.vibe_bucket_hint_tokens(field)
        if state["kind"] == "after_bucket":
            return ["and", "or", "to", "Enter  Add Rule", "Esc    Cancel"]
        if state["kind"] == "after_or":
            return [*self.vibe_bucket_hint_tokens(field), "more", "less"]
        if state["kind"] == "after_to":
            return self.vibe_bucket_hint_tokens(field)
        if state["kind"] == "after_and":
            return [name for _field, name, _presentation in VIBE_FEATURES]
        if state["kind"] == "invalid":
            return ["Enter  Add Rule", "Esc    Cancel"]
        return ["Esc    Cancel"]

    def vibe_bucket_hint_tokens(self, field: str | None) -> list[str]:
        if not field or field not in VIBE_FEATURE_BY_FIELD:
            return []
        return [self.clean_vibe_bucket_label(field, label) for label in VIBE_FEATURE_BY_FIELD[field][1]["labels"]]

    def vibe_rule_hint_state(self, text: str, forced_field: str | None) -> dict:
        raw = str(text or "")
        trailing_space = bool(raw) and raw[-1].isspace()
        normalized = self.normalize_vibe_text(raw)
        tokens = normalized.split()
        completed_tokens = tokens if trailing_space else tokens[:-1]
        if forced_field:
            return self.vibe_rule_hint_state_for_clause(completed_tokens, forced_field)

        if not completed_tokens:
            return {"kind": "feature"}
        if completed_tokens[-1] == "and":
            return {"kind": "after_and"}

        if "and" in completed_tokens:
            last_and = max(index for index, token in enumerate(completed_tokens) if token == "and")
            clause_tokens = completed_tokens[last_and + 1:]
            if not clause_tokens:
                return {"kind": "after_and"}
        else:
            clause_tokens = completed_tokens

        field, rest = self.extract_vibe_feature(" ".join(clause_tokens))
        if not field:
            return {"kind": "feature"}
        ambiguous_tempo = field == "tempo_ambiguous"
        hint_field = "tempo_bpm" if ambiguous_tempo else field
        rest_tokens = rest.split()
        if not rest_tokens:
            return {"kind": "after_feature", "field": hint_field, "ambiguous_tempo": ambiguous_tempo}
        if rest_tokens == ["is"]:
            return {"kind": "bucket", "field": hint_field, "ambiguous_tempo": ambiguous_tempo}
        if rest_tokens[0] == "stability" and ambiguous_tempo:
            hint_field = "tempo_variability"
            rest_tokens = rest_tokens[1:]
            if not rest_tokens:
                return {"kind": "after_feature", "field": hint_field}
            if rest_tokens == ["is"]:
                return {"kind": "bucket", "field": hint_field}
        if rest_tokens and rest_tokens[0] == "is":
            rest_tokens = rest_tokens[1:]
        return self.vibe_rule_hint_state_for_clause(rest_tokens, hint_field)

    def vibe_rule_hint_state_for_clause(self, completed_tokens: list[str], field: str) -> dict:
        if not completed_tokens:
            return {"kind": "bucket", "field": field}
        last = completed_tokens[-1]
        if last == "or":
            return {"kind": "after_or", "field": field}
        if last == "to":
            return {"kind": "after_to", "field": field}
        if last == "and":
            return {"kind": "after_and"}
        labels = list(VIBE_FEATURE_BY_FIELD[field][1]["labels"])
        first_bucket = self.resolve_vibe_level(field, labels, completed_tokens[0])
        if not first_bucket:
            return {"kind": "invalid", "field": field}
        return {"kind": "after_bucket", "field": field}

    def format_vibe_rule_line(self, rule: dict) -> str:
        field = rule.get("field")
        feature = VIBE_FEATURE_BY_FIELD.get(field)
        if not feature:
            return "Unknown rule"
        name, presentation = feature
        labels = list(presentation["labels"])
        if rule.get("display"):
            return f"{name:<16} is {rule['display']}"
        values = []
        for raw_score in rule.get("values") or []:
            try:
                score = int(raw_score)
            except (TypeError, ValueError):
                continue
            if 1 <= score <= len(labels):
                values.append(self.clean_vibe_bucket_label(field, str(labels[score - 1])))
        value_text = self.compact_vibe_values(field, values) if values else "None"
        return f"{name:<16} is {value_text}"

    def compact_vibe_values(self, field: str, values: list[str]) -> str:
        return " or ".join(values)

    def clean_vibe_bucket_label(self, field: str, label: str) -> str:
        feature_name = VIBE_FEATURE_BY_FIELD.get(field, ("", {}))[0].lower()
        words_to_strip = {
            "tempo_bpm": ["tempo"],
            "tempo_variability": ["tempo"],
            "onset_density": ["activity"],
            "energy_mean": ["intensity"],
            "impact": ["dynamics"],
            "spectral_brightness": ["tone"],
            "spectral_flatness": ["texture"],
        }.get(field, [])
        cleaned = str(label)
        for word in words_to_strip:
            cleaned = re.sub(rf"\b{re.escape(word)}\b", "", cleaned, flags=re.IGNORECASE).strip()
        return " ".join(cleaned.split()).title() if feature_name else str(label)

    async def start_vibe_add_rule(self) -> None:
        screen = self.query_one("#vibe_editor")
        section, group_index = screen.selected_target()
        self.vibe_rule_target = {"section": section, "group_index": group_index, "rule_index": None}
        self.vibe_pending_rule = {}
        self.input_mode = "VIBE_RULE_FEATURE"
        self.input_query = ""
        self.update_context_actions(self.vibe_editor_actions())
        self.show_status("Feature: _", persistent=True)

    async def start_vibe_edit_rule(self) -> None:
        screen = self.query_one("#vibe_editor")
        location = screen.selected_rule_location()
        if not location:
            self.show_status("Select a rule to edit.")
            return
        section, group_index, rule_index = location
        rule = self.vibe_rule_at(section, group_index, rule_index)
        if not rule:
            self.show_status("Select a rule to edit.")
            return
        self.vibe_rule_target = {"section": section, "group_index": group_index, "rule_index": rule_index}
        self.vibe_pending_rule = copy.deepcopy(rule)
        self.input_mode = "VIBE_RULE_FEATURE"
        self.input_query = ""
        self.update_context_actions(self.vibe_editor_actions())
        self.show_status("Feature: _", persistent=True)

    async def start_vibe_new_group(self) -> None:
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        rule.setdefault("groups", []).append({"match": "all", "rules": []})
        self.current_vibe_rule = rule
        screen = self.query_one("#vibe_editor")
        await screen.refresh_editor()
        for index, row in enumerate(getattr(screen, "_rows", [])):
            if row.get("kind") == "group_header" and row.get("group_index") == len(rule["groups"]) - 1:
                screen.set_list_index(index)
                break
        await self.start_vibe_add_rule()

    def vibe_rule_at(self, section: str, group_index: int | None, rule_index: int) -> dict | None:
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        try:
            if section == "required":
                return rule["required"]["rules"][rule_index]
            return rule["groups"][int(group_index or 0)]["rules"][rule_index]
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    async def delete_selected_vibe_item(self) -> None:
        location = self.query_one("#vibe_editor").selected_rule_location()
        row = self.query_one("#vibe_editor").selected_row()
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        if location:
            self.remove_vibe_rule_at(*location)
        elif row.get("kind") == "group_header":
            group_index = int(row.get("group_index") or 0)
            if len(rule.get("groups") or []) <= 1:
                rule["groups"][0]["rules"] = []
            else:
                del rule["groups"][group_index]
        else:
            self.show_status("Select a rule or group to delete.")
            return
        self.current_vibe_rule = rule
        await self.query_one("#vibe_editor").refresh_editor()
        self.show_status("Deleted.")

    async def commit_vibe_input(self) -> None:
        if self.input_mode == "VIBE_RULE_TARGET":
            await self.commit_vibe_rule_target()
            return
        if self.input_mode == "VIBE_RULE_FEATURE":
            parsed = self.parse_vibe_rule_text(self.input_query)
            if parsed:
                await self.apply_vibe_rules(parsed)
                return
            field = self.resolve_vibe_feature(self.input_query)
            if not field:
                self.show_status(f"Unknown feature: {self.input_query}", persistent=True)
                return
            self.vibe_pending_rule["field"] = field
            self.input_mode = "VIBE_RULE_LEVEL"
            self.input_query = ""
            self.update_context_actions(self.vibe_editor_actions())
            feature_name = VIBE_FEATURE_BY_FIELD[field][0]
            self.show_status(f"Feature: {feature_name} is _", persistent=True)
            return
        if self.input_mode == "VIBE_RULE_LEVEL":
            field = self.vibe_pending_rule.get("field")
            values = self.resolve_vibe_levels(field, self.input_query)
            if not values:
                parsed = self.parse_vibe_rule_text(f"{VIBE_FEATURE_BY_FIELD.get(field, ('',))[0]} is {self.input_query}")
                if parsed:
                    values = parsed[0].get("values", [])
            if not values:
                self.show_status(f"Unknown level: {self.input_query}", persistent=True)
                return
            self.vibe_pending_rule["op"] = "bucket_is"
            self.vibe_pending_rule["values"] = values
            if field:
                self.vibe_pending_rule["display"] = self.display_for_vibe_expression(field, self.input_query, values)
            await self.apply_vibe_rules([copy.deepcopy(self.vibe_pending_rule)])
            return

    async def abort_vibe_input(self) -> None:
        self.input_mode = "NORMAL"
        self.input_query = ""
        self.vibe_pending_rule = {}
        self.vibe_rule_target = {}
        self.vibe_rule_transfer = {}
        if self.current_vibe_rule:
            self.current_vibe_rule["groups"] = [
                group for group in self.current_vibe_rule.get("groups", [])
                if group.get("rules") or len(self.current_vibe_rule.get("groups", [])) == 1
            ] or [{"match": "all", "rules": []}]
            await self.query_one("#vibe_editor").refresh_editor()
        self.update_current_context_actions()
        self.show_status("Cancelled.")

    async def update_vibe_input_status(self) -> None:
        self.update_context_actions(self.vibe_editor_actions())
        if self.input_mode == "VIBE_RULE_FEATURE":
            self.show_status(f"Feature: {self.input_query}_", persistent=True)
        elif self.input_mode == "VIBE_RULE_LEVEL":
            field = self.vibe_pending_rule.get("field")
            feature_name = VIBE_FEATURE_BY_FIELD.get(field, ("Feature", {}))[0]
            self.show_status(f"Feature: {feature_name} is {self.input_query}_", persistent=True)
        elif self.input_mode == "VIBE_RULE_TARGET":
            action = self.vibe_rule_transfer.get("action", "Move").title()
            self.show_status(f"{action} to: {self.input_query}_", persistent=True)

    def normalize_vibe_text(self, text: str) -> str:
        value = str(text or "").lower().replace("-ish", " ish")
        value = re.sub(r"[^a-z0-9\s-]", " ", value)
        value = value.replace("-", " ")
        return " ".join(value.split())

    def parse_vibe_rule_text(self, text: str) -> list[dict]:
        value = self.normalize_vibe_text(text)
        if not value:
            return []
        clauses = [clause.strip() for clause in re.split(r"\band\b", value) if clause.strip()]
        rules = []
        for clause in clauses:
            parsed = self.parse_vibe_rule_clause(clause)
            if not parsed:
                return []
            rules.append(parsed)
        return rules

    def parse_vibe_rule_clause(self, clause: str) -> dict | None:
        field, rest = self.extract_vibe_feature(clause)
        if not field:
            return None
        if rest.startswith("is "):
            rest = rest[3:].strip()
        if not rest:
            return None
        if field == "tempo_ambiguous":
            resolved = self.resolve_ambiguous_tempo_field(rest)
            if not resolved:
                resolved = "tempo_bpm"
            field = resolved
        values = self.resolve_vibe_levels(field, rest)
        if not values:
            return None
        rule = self.vibe_rule_item(field, values)
        rule["display"] = self.display_for_vibe_expression(field, rest, values)
        return rule

    def display_for_vibe_expression(self, field: str, expression: str, values: list[int]) -> str:
        text = self.normalize_vibe_text(expression)
        labels = [str(label) for label in VIBE_FEATURE_BY_FIELD[field][1]["labels"]]
        if text.endswith(" ish"):
            center = self.resolve_vibe_level(field, labels, text[:-4].strip())
            if center:
                return f"{self.clean_vibe_bucket_label(field, labels[center - 1])}-ish"
        if " to " in f" {text} ":
            left, right = [part.strip() for part in text.split(" to ", 1)]
            start = self.resolve_vibe_level(field, labels, left)
            end = self.resolve_vibe_level(field, labels, right)
            if start and end:
                return f"{self.clean_vibe_bucket_label(field, labels[start - 1])} to {self.clean_vibe_bucket_label(field, labels[end - 1])}"
        if text.endswith(" or more"):
            start = self.resolve_vibe_level(field, labels, text[:-8].strip())
            if start:
                return f"{self.clean_vibe_bucket_label(field, labels[start - 1])} or more"
        if text.endswith(" or less"):
            end = self.resolve_vibe_level(field, labels, text[:-8].strip())
            if end:
                return f"{self.clean_vibe_bucket_label(field, labels[end - 1])} or less"
        return " or ".join(
            self.clean_vibe_bucket_label(field, labels[score - 1])
            for score in values
            if 1 <= score <= len(labels)
        )

    def extract_vibe_feature(self, clause: str) -> tuple[str | None, str]:
        aliases = sorted([*FEATURE_ALIASES.keys()], key=len, reverse=True)
        for alias in aliases:
            if clause == alias or clause.startswith(alias + " "):
                field = "tempo_ambiguous" if alias == "tempo" else FEATURE_ALIASES[alias]
                return field, clause[len(alias):].strip()
        for field, name, _presentation in VIBE_FEATURES:
            normalized = self.normalize_vibe_text(name)
            if clause == normalized or clause.startswith(normalized + " "):
                return field, clause[len(normalized):].strip()
        return None, clause

    def resolve_ambiguous_tempo_field(self, rest: str) -> str:
        labels = [self.normalize_vibe_bucket_token(label) for label in VIBE_FEATURE_BY_FIELD["tempo_variability"][1]["labels"]]
        words = set()
        for label in labels:
            words.update(label.split())
        tokens = set(self.normalize_vibe_text(rest).split())
        if tokens & words:
            return "tempo_variability"
        return "tempo_bpm"

    def resolve_vibe_feature(self, text: str) -> str | None:
        value = self.normalize_vibe_text(text)
        if value == "tempo":
            return "tempo_bpm"
        if value in FEATURE_ALIASES:
            return FEATURE_ALIASES[value]
        for field, name, _presentation in VIBE_FEATURES:
            normalized = self.normalize_vibe_text(name)
            if value == normalized or value == normalized.replace("felt ", ""):
                return field
        return None

    def resolve_vibe_levels(self, field: str | None, text: str) -> list[int]:
        if not field or field not in VIBE_FEATURE_BY_FIELD:
            return []
        expression = self.normalize_vibe_text(text)
        labels = [str(label) for label in VIBE_FEATURE_BY_FIELD[field][1]["labels"]]
        if " to " in f" {expression} ":
            left, right = [part.strip() for part in expression.split(" to ", 1)]
            start = self.resolve_vibe_level(field, labels, left)
            end = self.resolve_vibe_level(field, labels, right)
            if start and end:
                lo, hi = sorted((start, end))
                return list(range(lo, hi + 1))
        if expression.endswith(" or more"):
            start = self.resolve_vibe_level(field, labels, expression[:-8].strip())
            if start:
                return list(range(start, len(labels) + 1))
        if expression.endswith(" or less"):
            end = self.resolve_vibe_level(field, labels, expression[:-8].strip())
            if end:
                return list(range(1, end + 1))
        parts = [part.strip() for part in expression.replace(",", " or ").split(" or ") if part.strip()]
        values = []
        for part in parts:
            if part.endswith(" ish"):
                center = self.resolve_vibe_level(field, labels, part[:-4].strip())
                if center:
                    for score in (center - 1, center, center + 1):
                        if 1 <= score <= len(labels) and score not in values:
                            values.append(score)
                continue
            score = self.resolve_vibe_level(field, labels, part)
            if score and score not in values:
                values.append(score)
        return values

    def resolve_vibe_level(self, field: str, labels: list[str], text: str) -> int | None:
        value = self.normalize_vibe_text(text)
        generic = GENERIC_BUCKETS_3 if len(labels) == 3 else GENERIC_BUCKETS_5
        if value in generic and generic[value] <= len(labels):
            return generic[value]
        for index, label in enumerate(labels, 1):
            normalized = self.normalize_vibe_bucket_token(label)
            words = normalized.split()
            if value == normalized or value in words:
                return index
        return None

    def normalize_vibe_bucket_token(self, label: str) -> str:
        return self.normalize_vibe_text(label)

    async def apply_vibe_rules(self, rules: list[dict]) -> None:
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        target = self.vibe_rule_target
        section = target.get("section")
        group_index = target.get("group_index")
        rule_index = target.get("rule_index")
        destination = rule["required"]["rules"] if section == "required" else rule["groups"][int(group_index or 0)]["rules"]
        if rule_index is None:
            for item in rules:
                self.upsert_vibe_rule(destination, item)
        else:
            destination[int(rule_index)] = rules[0]
            for item in rules[1:]:
                self.upsert_vibe_rule(destination, item)
            self.merge_duplicate_vibe_rules(destination)
        self.current_vibe_rule = rule
        self.input_mode = "NORMAL"
        self.input_query = ""
        self.vibe_pending_rule = {}
        self.vibe_rule_target = {}
        await self.query_one("#vibe_editor").refresh_editor()
        self.update_current_context_actions()
        self.show_status("Status: Ready")

    def upsert_vibe_rule(self, rules: list[dict], new_rule: dict) -> None:
        for existing in rules:
            if existing.get("field") == new_rule.get("field"):
                merged = sorted(set(int(v) for v in existing.get("values", [])) | set(int(v) for v in new_rule.get("values", [])))
                existing["values"] = merged
                existing["op"] = "bucket_is"
                existing.pop("display", None)
                return
        rules.append(new_rule)

    def merge_duplicate_vibe_rules(self, rules: list[dict]) -> None:
        merged: list[dict] = []
        for rule in rules:
            self.upsert_vibe_rule(merged, rule)
        rules[:] = merged

    async def loosen_selected_vibe_rule(self) -> None:
        location = self.query_one("#vibe_editor").selected_rule_location()
        if not location:
            self.show_status("Select a rule to loosen.")
            return
        rule = self.vibe_rule_at(*location)
        if not rule:
            self.show_status("Select a rule to loosen.")
            return
        labels = VIBE_FEATURE_BY_FIELD[rule["field"]][1]["labels"]
        expanded = set()
        for raw in rule.get("values") or []:
            score = int(raw)
            expanded.update(value for value in (score - 1, score, score + 1) if 1 <= value <= len(labels))
        rule["values"] = sorted(expanded)
        await self.query_one("#vibe_editor").refresh_editor()
        self.show_status("Rule loosened.")

    async def start_vibe_rule_transfer(self, action: str) -> None:
        location = self.query_one("#vibe_editor").selected_rule_location()
        if not location:
            self.show_status("Select a rule first.")
            return
        source_rule = self.vibe_rule_at(*location)
        if not source_rule:
            self.show_status("Select a rule first.")
            return
        self.vibe_rule_transfer = {"action": action, "location": location}
        self.input_mode = "VIBE_RULE_TARGET"
        self.input_query = ""
        self.update_context_actions(self.vibe_editor_actions())
        self.show_status(self.vibe_rule_transfer_status(action, source_rule), persistent=True)

    def vibe_rule_target_options(self) -> list[str]:
        groups = self.current_vibe_rule.get("groups", []) if self.current_vibe_rule else []
        return ["Required Rules", *[f"Group {index + 1}" for index in range(len(groups))], "Esc    Cancel"]

    async def commit_vibe_rule_target(self) -> None:
        target = self.query_one("#vibe_editor").selected_target()
        source = self.vibe_rule_transfer.get("location")
        source_rule = self.vibe_rule_at(*source)
        if not source_rule:
            await self.abort_vibe_input()
            return
        action = self.vibe_rule_transfer.get("action")
        rule_copy = copy.deepcopy(source_rule)
        if action == "move":
            if self.vibe_rule_target_matches_source(target, source):
                self.input_mode = "NORMAL"
                self.input_query = ""
                self.vibe_rule_transfer = {}
                self.update_current_context_actions()
                self.show_status("Rule already there.")
                return
            target = self.adjust_vibe_rule_target_after_move(target, source)
            self.remove_vibe_rule_at(*source)
        self.add_vibe_rule_to_target(target[0], target[1], rule_copy)
        self.input_mode = "NORMAL"
        self.input_query = ""
        self.vibe_rule_transfer = {}
        await self.query_one("#vibe_editor").refresh_editor()
        self.update_current_context_actions()
        self.show_status("Rule moved." if action == "move" else "Rule copied.")

    def vibe_rule_transfer_status(self, action: str, rule: dict) -> str:
        verb = "Moving" if action == "move" else "Copying"
        rule_text = " ".join(self.format_vibe_rule_line(rule).strip().split())
        return f"{verb}: {rule_text}"

    def vibe_rule_target_matches_source(self, target: tuple[str, int | None], source: tuple[str, int | None, int]) -> bool:
        return target[0] == source[0] and target[1] == source[1]

    def adjust_vibe_rule_target_after_move(
        self,
        target: tuple[str, int | None],
        source: tuple[str, int | None, int],
    ) -> tuple[str, int | None]:
        if source[0] != "group" or target[0] != "group":
            return target
        source_group_index = int(source[1] or 0)
        target_group_index = int(target[1] or 0)
        groups = (self.current_vibe_rule or {}).get("groups") or []
        source_group_rules = groups[source_group_index].get("rules", []) if source_group_index < len(groups) else []
        source_group_will_be_removed = len(source_group_rules) <= 1 and len(groups) > 1
        if source_group_will_be_removed and source_group_index < target_group_index:
            return "group", target_group_index - 1
        return target

    def resolve_vibe_rule_target(self, text: str) -> tuple[str, int | None] | None:
        value = self.normalize_vibe_text(text)
        if value in ("required", "required rules", "master"):
            return "required", None
        match = re.search(r"(\d+)", value)
        if match:
            index = int(match.group(1)) - 1
            groups = self.current_vibe_rule.get("groups", []) if self.current_vibe_rule else []
            if 0 <= index < len(groups):
                return "group", index
        return None

    def add_vibe_rule_to_target(self, section: str, group_index: int | None, rule: dict) -> None:
        root = self.current_vibe_rule or self.empty_vibe_rule()
        destination = root["required"]["rules"] if section == "required" else root["groups"][int(group_index or 0)]["rules"]
        self.upsert_vibe_rule(destination, rule)
        self.current_vibe_rule = root

    def remove_vibe_rule_at(self, section: str, group_index: int | None, rule_index: int) -> None:
        root = self.current_vibe_rule or self.empty_vibe_rule()
        if section == "required":
            del root["required"]["rules"][int(rule_index)]
        else:
            group = root["groups"][int(group_index or 0)]
            del group["rules"][int(rule_index)]
            if not group["rules"] and len(root["groups"]) > 1:
                del root["groups"][int(group_index or 0)]
        self.current_vibe_rule = root

    async def preview_vibe_matches(self) -> None:
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        self.current_vibe_preview_items = self.collection_manager.search_vibe_rule(rule)
        self.query_one(ContentSwitcher).current = "vibe_preview"
        self._nav_stack.append("vibe_preview")
        screen = self.query_one("#vibe_preview")
        screen._offset = 0
        await screen.refresh_preview()
        self._focus_main_content("vibe_preview")
        self.update_current_context_actions()

    async def save_vibe_collection(self) -> None:
        name = (self.current_vibe_name or "").strip()
        rule = self.current_vibe_rule or self.empty_vibe_rule()
        if not name:
            self.show_status("Vibe name cannot be empty.")
            return
        if not self.vibe_rule_has_any_rules(rule):
            self.show_status("Add at least one vibe rule before saving.")
            return
        rule["groups"] = [group for group in rule.get("groups", []) if group.get("rules")]
        new_id = self.collection_manager.create_vibe_collection(name, rule)
        collection = next(
            (item for item in self.collection_manager.get_all_collections(include_archived=True) if item.get("collection_id") == new_id),
            {"collection_id": new_id, "name": name, "group_id": None},
        )
        self.current_vibe_rule = None
        self.current_vibe_name = None
        await self.start_collection_group_flow(collection, after_create=True, origin_actions=self.NAV_MAP["collections"][2])

    def vibe_rule_has_any_rules(self, rule: dict) -> bool:
        if rule.get("required", {}).get("rules"):
            return True
        return any(group.get("rules") for group in rule.get("groups") or [])

    def vibe_counts_and_warnings(self, rule: dict) -> tuple[dict, list[str]]:
        counts = self.collection_manager.vibe_rule_counts(rule)
        warnings = []
        if counts.get("total", 0) == 0:
            warnings.append("Total matches 0 tracks.")
        for index, count in enumerate(counts.get("groups", []), 1):
            if count == 0:
                warnings.append(f"Group {index} matches 0 tracks.")
        if self.vibe_rule_uses_tempo(rule):
            warnings.append("Tempo rules may change after re-analysis.")
        return counts, warnings

    def vibe_rule_uses_tempo(self, rule: dict) -> bool:
        fields = [item.get("field") for item in rule.get("required", {}).get("rules", [])]
        for group in rule.get("groups") or []:
            fields.extend(item.get("field") for item in group.get("rules", []))
        return "tempo_bpm" in fields or "tempo_variability" in fields

    def format_vibe_rule_summary(self, rule: dict, *, include_counts: bool = False) -> str:
        counts, warnings = self.vibe_counts_and_warnings(rule) if include_counts else ({}, [])
        lines = []
        required_suffix = f" ({counts.get('required', 0)} matches)" if include_counts else ""
        lines.append(f"Required Rules{required_suffix}")
        required_rules = rule.get("required", {}).get("rules", [])
        lines.extend([f"  {self.format_vibe_rule_line(item).strip()}" for item in required_rules] or ["  none"])
        for index, group in enumerate(rule.get("groups") or [], 1):
            group_counts = counts.get("groups", []) if include_counts else []
            suffix = f" ({group_counts[index - 1]} matches)" if index - 1 < len(group_counts) else ""
            lines.append(f"Group {index}{suffix}")
            lines.extend([f"  {self.format_vibe_rule_line(item).strip()}" for item in group.get("rules", [])] or ["  none"])
        if include_counts:
            lines.append(f"Total: {counts.get('total', 0)} matches")
            lines.extend(warnings)
        return "\n".join(lines)

    def fetch_and_save_vibe_collection(self, collection: dict, job_context=None) -> None:
        count = self.collection_manager.refresh_vibe_collection(collection.get("collection_id"), job_context=job_context)
        if job_context:
            job_context.update_current_item(f"Matched {count} tracks")

    def is_vibe_collection(self, collection: dict | None = None) -> bool:
        collection = collection or self.get_selected_collection()
        if not collection:
            return False
        return str(collection.get("source_type") or "").lower() == "vibe" or str(collection.get("collection_type") or "").lower() == "vibe"

    async def create_refresh_vibe_job(self, collection: dict) -> None:
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Refresh Vibe: {collection.get('name', 'Vibe')}",
            target_func=self.fetch_and_save_vibe_collection,
            collection=collection,
        )
        self.show_status(f"Refreshing vibe: {collection.get('name', 'Vibe')}.")
        await self.refresh_jobs_view()
