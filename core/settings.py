import yaml
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.audio_analysis import DEFAULT_AUDIO_ANALYSIS_WORKER_PROFILE, audio_analysis_profile_options

logger = logging.getLogger(__name__)

class SettingsManager:
    """
    Single source of truth for configuration management.
    Handles loading, saving, and schema-driven validation of config.yaml.
    The UI should never interact with YAML directly.
    """
    
    # Metadata used by TUIs/GUIs to auto-generate settings forms
    SCHEMA = {
        "general": {
            "database_path": {"type": "str", "path": "database_path", "default": "./database.db", "desc": "SQLite database path."},
            "library_root": {"type": "str", "path": "library_root", "default": "./library", "desc": "Local music library root."},
        },
        "collections": {
            "stale_refresh_days": {"type": "int", "min": 1, "default": 7, "desc": "External collections older than this need refresh."},
        },
        "audio_analysis": {
            "worker_profile": {
                "type": "choice",
                "default": DEFAULT_AUDIO_ANALYSIS_WORKER_PROFILE,
                "options": audio_analysis_profile_options(),
                "desc": "Process/thread profile for batch audio analysis.",
            },
        },
        "spotify": {
            "mode": {"type": "choice", "default": "disabled", "options": ["disabled", "enabled"], "desc": "Optional Spotify integration. Requires your own eligible developer app."},
            "client_id": {"type": "str", "default": "", "desc": "Spotify API client ID."},
            "client_secret": {"type": "str", "default": "", "desc": "Spotify API client secret."},
        },
        "matching": {
            "auto_accept_threshold": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.69, "desc": "Auto-approve matches above this score."},
            "similarity_variance": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.10, "desc": "Allowed variance for grouping tracks."},
            "max_review_candidates": {"type": "int", "min": 1, "default": 5, "desc": "Max candidates to show in review UI."},
            "weights": {
                "title": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.33, "desc": "Title similarity weight."},
                "artist": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.33, "desc": "Artist similarity weight."},
                "duration": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.34, "desc": "Duration similarity weight."},
            },
        },
        "youtube": {
            "max_results": {"type": "int", "min": 1, "default": 10, "desc": "Max tracks to fetch from infinite mixes."},
            "browser_cookies": {"type": "str", "default": "", "desc": "Browser for cookie extraction (e.g., firefox, chrome)."}
        },
        "exports": {
            "roots": {"type": "list", "default": ["./exports_root"], "desc": "Valid base directories for exports."}
        }
    }

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self):
        """Reads config.yaml from disk."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Failed to load config.yaml: {e}")
                self._config = {}
        else:
            self._config = {}

        # Ensure base dictionary structures exist to prevent KeyErrors
        if "matching" not in self._config: self._config["matching"] = {}
        if "penalties" not in self._config["matching"]: self._config["matching"]["penalties"] = {}
        if "spotify" not in self._config: self._config["spotify"] = {}
        if "collections" not in self._config: self._config["collections"] = {}
        if "audio_analysis" not in self._config: self._config["audio_analysis"] = {}
        if "youtube" not in self._config: self._config["youtube"] = {}
        if "exports" not in self._config: self._config["exports"] = {}

    def save(self):
        """Writes current configuration state back to config.yaml."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, sort_keys=False)
            logger.info("Configuration saved successfully.")
        except Exception as e:
            logger.error(f"Failed to save config.yaml: {e}")

    def get(self, path: str, default: Any = None) -> Any:
        """Retrieves a value using dot notation (e.g., 'matching.auto_accept_threshold')."""
        keys = path.split('.')
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, path: str, value: Any):
        """Validates and sets a value using dot notation."""
        value = self._validate(path, value)
        
        keys = path.split('.')
        target = self._config
        for k in keys[:-1]:
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value

    def _validate(self, path: str, value: Any) -> Any:
        """Validates a value against the internal SCHEMA before applying it."""
        node = self.get_schema_node(path)
        if not node:
            return value

        if "type" not in node: return value

        v_type = node["type"]
        try:
            if v_type == "float":
                val = float(value)
                if "min" in node and val < node["min"]: raise ValueError(f"Must be >= {node['min']}")
                if "max" in node and val > node["max"]: raise ValueError(f"Must be <= {node['max']}")
                return val
            elif v_type == "int":
                val = int(value)
                if "min" in node and val < node["min"]: raise ValueError(f"Must be >= {node['min']}")
                if "max" in node and val > node["max"]: raise ValueError(f"Must be <= {node['max']}")
                return val
            elif v_type == "list":
                if not isinstance(value, list): raise ValueError("Must be a list")
                return value
            elif v_type == "choice":
                val = str(value)
                options = node.get("options", [])
                valid_values = {
                    str(option.get("value")) if isinstance(option, dict) else str(option)
                    for option in options
                }
                if valid_values and val not in valid_values:
                    raise ValueError(f"Must be one of: {', '.join(sorted(valid_values))}")
                return val
            elif v_type == "str":
                return str(value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Validation failed for '{path}': {e}")
            
        return value

    def get_schema_node(self, path: str) -> Optional[Dict[str, Any]]:
        """Finds schema metadata for an actual config path."""
        def walk(prefix: str, tree: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            for key, meta in tree.items():
                if not isinstance(meta, dict):
                    continue
                next_path = f"{prefix}.{key}" if prefix else key
                if "type" in meta:
                    actual_path = meta.get("path", next_path)
                    if actual_path == path:
                        return meta
                else:
                    found = walk(next_path, meta)
                    if found:
                        return found
            return None

        return walk("", self.SCHEMA)

    def iter_schema_entries(self, category: str):
        """Yields display label, actual path, and metadata for a category."""
        tree = self.SCHEMA.get(category, {})

        def walk(prefix: str, node: Dict[str, Any]):
            for key, meta in node.items():
                if not isinstance(meta, dict):
                    continue
                label = f"{prefix}.{key}" if prefix else key
                if "type" in meta:
                    default_path = f"{category}.{label}"
                    yield label, meta.get("path", default_path), meta
                else:
                    yield from walk(label, meta)

        yield from walk("", tree)

    def reset(self, category: str):
        """Resets a specific category to its schema defaults."""
        if category in self.SCHEMA:
            for _label, path, meta in self.iter_schema_entries(category):
                if "default" in meta:
                    self.set(path, meta["default"])
            logger.info(f"Reset category '{category}' to defaults.")

    def reset_all(self):
        """Resets all configured categories to their defaults."""
        for category in self.SCHEMA.keys():
            self.reset(category)

    def get_categories(self) -> List[str]:
        """Returns top-level config categories."""
        return list(self.SCHEMA.keys())

    def get_settings(self, category: str) -> dict:
        """Returns current values for a specific category."""
        return self.get(category, {})

    def get_schema(self) -> dict:
        """Exposes the schema metadata to UI generators."""
        return self.SCHEMA

    # --- First-Class Penalty Management ---

    def get_penalties(self) -> Dict[str, float]:
        """Returns the dictionary of matching penalties."""
        return self.get("matching.penalties", {})

    def add_penalty(self, keyword: str, value: float):
        """Validates and adds a penalty keyword."""
        val = float(value)
        penalties = self.get_penalties()
        penalties[keyword] = val
        self.set("matching.penalties", penalties)

    def remove_penalty(self, keyword: str):
        """Removes a penalty keyword."""
        penalties = self.get_penalties()
        if keyword in penalties:
            del penalties[keyword]
            self.set("matching.penalties", penalties)
