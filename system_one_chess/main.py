import os
from pathlib import Path

from fastapi import FastAPI
from pico_boot import init
from pico_ioc import DictSource, EnvSource, configuration

ENV_FILE = Path(__file__).parent.with_name(".env")


def load_env(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def create_app() -> FastAPI:
    load_env()
    container = init(modules=["system_one_chess"], config=configuration(EnvSource(), DictSource({})))
    return container.get(FastAPI)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "system_one_chess.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
