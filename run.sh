#!/usr/bin/env bash
# Запуск: ./run.sh [-c config.ini] [--load-order] [--no-image]
# Тесты:  ./run.sh test
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" = "test" ]; then
    python3 -m unittest discover -s tests -v
else
    python3 src/main.py "$@"
fi
