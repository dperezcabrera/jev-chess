import secrets
from dataclasses import dataclass, field

from pico_ioc import configured


@configured(prefix="OPENROUTER_", mapping="flat")
@dataclass
class OpenRouterSettings:
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api"
    timeout_seconds: float = 30.0


@configured(prefix="AI_GATEWAY_", mapping="flat")
@dataclass
class VercelSettings:
    api_key: str = ""
    base_url: str = "https://ai-gateway.vercel.sh/typesafe"
    timeout_seconds: float = 30.0


@configured(prefix="LAYA_", mapping="flat")
@dataclass
class LayaSettings:
    model: str = "convaiinnovations/laya"
    device: str = ""


@configured(prefix="JEV_", mapping="flat")
@dataclass
class JevSettings:
    provider: str = ""
    model: str = ""


@configured(prefix="ILLEGAL_MOVES_", mapping="flat")
@dataclass
class IllegalMovesSettings:
    limit: int = 2


@configured(prefix="MODELS_", mapping="flat")
@dataclass
class ModelsSettings:
    file: str = ""


@configured(prefix="SESSION_", mapping="flat")
@dataclass
class SessionSettings:
    secret: str = field(default_factory=lambda: secrets.token_hex(32))
