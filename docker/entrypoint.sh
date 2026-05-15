#!/bin/bash
set -e

mkdir -p /app/logs

exec "$@"
