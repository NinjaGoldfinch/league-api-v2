FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip install --upgrade pip

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install -e .

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "league_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
