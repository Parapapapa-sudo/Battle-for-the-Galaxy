FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home app && chown -R app /srv
USER app

# По умолчанию — HTTP API; worker и vk-adapter переопределяют CMD в compose.
CMD ["uvicorn", "adapters.http.main:create_app_from_settings", "--factory", "--host", "0.0.0.0", "--port", "8000"]
