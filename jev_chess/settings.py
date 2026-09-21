import secrets
from dataclasses import dataclass, field

from pico_ioc import configured


@configured(prefix="OPENROUTER_", mapping="flat")
@dataclass
class OpenRouterSettings:
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api"
    timeout_seconds: float = 30.0


@configured(prefix="JEV_", mapping="flat")
@dataclass
class JevSettings:
    model: str = "jev-latest"


@configured(prefix="SESSION_", mapping="flat")
@dataclass
class SessionSettings:
    secret: str = field(default_factory=lambda: secrets.token_hex(32))
