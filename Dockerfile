FROM python:3.13-slim

# git is the whole point of this service.
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-dev --no-install-project

# Then the package itself. Project data, config and database are NOT baked
# in — they are mounted at runtime (see ediarum.deploy).
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-cache --no-dev --no-editable

# Read access to the project repositories at runtime: a token mounted at
# /run/secrets/git_token (see docker/git-credential-token).
COPY docker/git-credential-token /usr/local/bin/git-credential-token
RUN git config --system credential.helper token

# `ediarum-fileservice` is on PATH via /opt/venv/bin. Port 8000 inside the
# container, as ediarum.deploy and the watchers' FILESERVICE_URL expect.
ENV PORT=8000
EXPOSE 8000
CMD ["ediarum-fileservice"]
