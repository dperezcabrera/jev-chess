FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jev_chess ./jev_chess
RUN pip install --no-cache-dir .
RUN useradd --create-home player
USER player
EXPOSE 8000
ENTRYPOINT ["jev-chess"]
