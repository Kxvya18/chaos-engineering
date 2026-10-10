# chaosctl as a container on the demo's network ("deployed" mode).
# The docker CLI binary is copied from the official docker CLI image; the daemon is the host's
# (Docker Desktop) via the mounted socket. See docs/SAFETY.md "Deployed mode".
FROM docker:28-cli AS dockercli

FROM python:3.12-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config
COPY scenarios ./scenarios
COPY fixes ./fixes
COPY tools ./tools
ENTRYPOINT ["chaosctl"]
CMD ["dashboard", "--host", "0.0.0.0", "--port", "8090"]
