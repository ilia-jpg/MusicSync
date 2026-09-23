import yaml
from pathlib import Path

class ConfigManager:
    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_or_create()

    def _load_or_create(self):
        if not self.config_path.exists():
            default_config = {
                "database_path": "./database.db",
                "library_root": "./library",
                "spotify": {
                    "mode": "disabled",
                    "client_id": "",
                    "client_secret": ""
                },
                "youtube": {
                    "max_results": 10
                },
                "audio_analysis": {
                    "worker_profile": "1p8t"
                },
                "matching": {
                    "auto_accept_threshold": 0.90,
                    "similarity_variance": 0.05,
                    "max_review_candidates": 3,
                    "weights": {
                        "title": 0.33,
                        "artist": 0.33,
                        "duration": 0.34
                    },
                    "penalties": {
                        "live": 0.15,
                        "karaoke": 0.5,
                        "cover": 0.3,
                        "instrumental": 0.2,
                        "official": -0.15
                    }
                },
                "exports": {
                    "roots": ["../exports_root"]
                }
            }
            with open(self.config_path, 'w') as f:
                yaml.dump(default_config, f, sort_keys=False)
            return default_config
        
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def get(self, key_path, default=None):
        keys = key_path.split('.')
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
