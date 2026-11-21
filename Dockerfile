FROM node:24-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; npm ci --strict-ssl=true
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; pip install --no-cache-dir -r requirements.txt
COPY server/ ./server/
COPY runner/ ./runner/
COPY examples/ ./examples/
COPY scripts/serve.py ./scripts/serve.py
COPY --from=web /web/dist ./web/dist
ENV PYTHONPATH=/app/server:/app/runner:/app
RUN useradd --create-home guard && mkdir /data && chown -R guard:guard /app /data
USER guard
EXPOSE 8080
CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8080"]
