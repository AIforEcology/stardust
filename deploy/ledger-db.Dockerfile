# PostgreSQL 17 + PostGIS 3 for the Data Center Resource Ledger, for linux/arm64 (Oracle Ampere A1)
# and amd64. postgis/postgis:17-3.5 publishes amd64 only (checked 2026-11-03), so PostGIS comes from
# the PostgreSQL apt repository (apt.postgresql.org) that the official postgres image already uses.
#   docker buildx build --platform linux/amd64,linux/arm64 -f deploy/ledger-db.Dockerfile deploy
FROM postgres:17-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}-postgis-3" \
 && rm -rf /var/lib/apt/lists/*
