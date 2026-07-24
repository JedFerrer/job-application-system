# ESCO catalog attribution

The full ESCO v1.2.1 English source snapshot and generated ESCO runtime catalog
are still pending. `catalog.v1.2.1.json.gz` is an interim compatibility artifact
generated from the repository's legacy `_SKILL_ALIASES` map. It preserves the
existing local canonical labels and aliases with deterministic `local:` skill
IDs; it does not claim full ESCO coverage.

The compatibility artifact was generated offline by
`app.scripts.build_esco_catalog.build_compatibility_catalog`. It makes no
runtime network calls and has no external ESCO source URL or license claim.
When the full ESCO source is supplied, record its exact release, download URL,
license terms, source checksum, build timestamp, and catalog-builder version
before replacing this interim artifact.
