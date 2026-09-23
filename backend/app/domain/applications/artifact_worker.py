"""Private subprocess entry point for untrusted artifact parsing."""

from __future__ import annotations

import base64
import json
import sys

from app.domain.applications.artifacts import ArtifactValidationError, _validation_worker


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read())
        report = _validation_worker(
            base64.b64decode(request["content"], validate=True),
            request["format"], request["expected"], request["document_type"],
        )
        sys.stdout.write(json.dumps({"report": report}, separators=(",", ":")))
        return 0
    except ArtifactValidationError as exc:
        sys.stdout.write(json.dumps({"failure_category": exc.category.value}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
