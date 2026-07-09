# Leos HTTP service image. Build: docker build -t leos-server .
# The container refuses to start without LEOS_SERVER_API_KEY (fail-closed).
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /build/dist

FROM python:3.12-slim
RUN groupadd --gid 65532 leos \
    && useradd --uid 65532 --gid 65532 --create-home leos \
    && mkdir -p /data /inbox \
    && chown 65532:65532 /data /inbox
COPY --from=build /build/dist/*.whl /tmp/leos/
RUN pip install --no-cache-dir "$(echo /tmp/leos/*.whl)[server,postgres,observability]" && rm -rf /tmp/leos

USER 65532:65532
WORKDIR /home/leos
# Secrets (LEOS_SERVER_API_KEY, LEOS_APPROVAL_HMAC_SECRET, LEOS_GITHUB_TOKEN)
# are injected at runtime via env_file/secrets - never baked into the image.
ENV LEOS_SERVER_HOST=0.0.0.0 \
    LEOS_SERVER_DATA_DIR=/data
VOLUME ["/data", "/inbox"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]
ENTRYPOINT ["leos", "serve"]
