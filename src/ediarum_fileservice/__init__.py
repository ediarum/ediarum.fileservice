"""ediarum.fileservice: keeps each project's XML on the
shared mount in step with webdav and git — the mount's only writer.
"""


PORT_ENV = "PORT"
DEFAULT_PORT = 8002  # local runs; the Docker image sets PORT=8000


def main() -> None:
    "``ediarum-fileservice``: run the app on ``$PORT`` (default 8002)."
    import os

    import uvicorn

    port = int(os.getenv(PORT_ENV, DEFAULT_PORT))
    uvicorn.run("ediarum_fileservice.app:app", host="0.0.0.0", port=port)
