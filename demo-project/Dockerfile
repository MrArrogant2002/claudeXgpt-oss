# Minimal image for the Nimbus API (stdlib only — no pip install needed).
FROM python:3.12-slim

WORKDIR /app
COPY services/api /app/services/api
ENV PYTHONPATH=/app/services/api

EXPOSE 8000
CMD ["python", "-c", "from nimbus_api import Api; print('nimbus api ready')"]
