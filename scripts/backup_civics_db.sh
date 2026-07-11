#!/bin/sh
set -eu
exec "${CIVICS_PYTHON:-/opt/civics/.venv/bin/python}" /opt/civics/scripts/backup_civics_db.py
