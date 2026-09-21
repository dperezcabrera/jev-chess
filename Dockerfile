FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
RUN useradd --create-home player
COPY pyproject.toml LICENSE ./
RUN mkdir jev_chess && touch jev_chess/__init__.py README.md \
    && pip install --no-cache-dir . \
    && pip uninstall -y jev-chess \
    && rm -rf jev_chess README.md build *.egg-info
COPY jev_chess ./jev_chess
USER player
EXPOSE 8000
ENTRYPOINT ["python", "-m", "jev_chess.main"]
