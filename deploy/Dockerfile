# chaosctl (agents + dashboard) as a container next to the demo ("deployed" mode).
# The docker CLI and its compose plugin come from the official docker CLI image; the daemon is the host's
# (Docker Desktop) via the mounted socket. See docs/SAFETY.md "Deployed mode".
FROM docker:28-cli AS dockercli

FROM python:3.12-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config
COPY scenarios ./scenarios
COPY fixes ./fixes
COPY tools ./tools
COPY docs ./docs
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/status', timeout=4)"
ENTRYPOINT ["chaosctl"]
CMD ["dashboard", "--host", "0.0.0.0", "--port", "8090"]
