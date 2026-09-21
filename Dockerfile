FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY pyproject.toml LICENSE ./
RUN mkdir jev_chess && touch jev_chess/__init__.py README.md && pip install --no-cache-dir .
COPY README.md ./
COPY jev_chess ./jev_chess
RUN pip install --no-cache-dir --no-deps --force-reinstall .
RUN useradd --create-home player
USER player
EXPOSE 8000
ENTRYPOINT ["jev-chess"]
