"""Save artifacts to a user-provided GCS bucket."""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timezone

from google.cloud import storage

logger = logging.getLogger(__name__)


class GCSStorage:
    def __init__(self, bucket_name: str, project: str | None = None) -> None:
        bucket_name = (bucket_name or "").strip()
        if not bucket_name:
            raise ValueError("GCS bucket name must not be empty.")
        self.bucket_name = bucket_name
        self._client = storage.Client(project=project) if project else storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def check_access(self) -> None:
        """Raise a clear error early if the bucket is missing or inaccessible."""
        if not self._bucket.exists():
            raise ValueError(
                f"GCS bucket '{self.bucket_name}' does not exist or is not accessible."
            )

    def upload_text(self, path: str, content: str) -> str:
        content_type = mimetypes.guess_type(path)[0] or "text/plain"
        blob = self._bucket.blob(path)
        blob.upload_from_string(content, content_type=content_type)
        uri = f"gs://{self.bucket_name}/{path}"
        logger.info("Uploaded %s (%d bytes)", uri, len(content))
        return uri

    def upload_bundle(self, prefix: str, artifacts: dict[str, str]) -> dict[str, str]:
        """Upload a {filename: content} bundle under a prefix. Returns gs URIs."""
        uris: dict[str, str] = {}
        for name, content in artifacts.items():
            uris[name] = self.upload_text(f"{prefix}/{name}", content)
        return uris


def make_run_prefix(gold_dataset: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"data-modeler/{gold_dataset}/{ts}"
