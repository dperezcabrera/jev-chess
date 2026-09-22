FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
RUN useradd --create-home player
COPY pyproject.toml LICENSE ./
RUN mkdir system_one_chess && touch system_one_chess/__init__.py README.md \
    && pip install --no-cache-dir . \
    && pip uninstall -y system-one-chess \
    && rm -rf system_one_chess README.md build *.egg-info
COPY system_one_chess ./system_one_chess
USER player
EXPOSE 8000
ENTRYPOINT ["python", "-m", "system_one_chess.main"]
