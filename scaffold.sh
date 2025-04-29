#!/bin/bash

# Exit on error
set -e

echo "Creating funding_curve project structure..."

# Create main directories
mkdir -p funding_curve/funding_curve/{collectors,builders,storage,utils,pipelines}
mkdir -p funding_curve/tests

# Create files in root directory
touch funding_curve/README.md
touch funding_curve/pyproject.toml

# Create files in funding_curve directory
touch funding_curve/funding_curve/__init__.py
touch funding_curve/funding_curve/config.py

# Create files in collectors directory
touch funding_curve/funding_curve/collectors/__init__.py
touch funding_curve/funding_curve/collectors/base.py
touch funding_curve/funding_curve/collectors/binance.py
touch funding_curve/funding_curve/collectors/bybit.py

# Create files in builders directory
touch funding_curve/funding_curve/builders/__init__.py
touch funding_curve/funding_curve/builders/curve.py

# Create files in storage directory
touch funding_curve/funding_curve/storage/__init__.py
touch funding_curve/funding_curve/storage/db.py
touch funding_curve/funding_curve/storage/schemas.py

# Create files in utils directory
touch funding_curve/funding_curve/utils/__init__.py
touch funding_curve/funding_curve/utils/time.py

# Create files in pipelines directory
touch funding_curve/funding_curve/pipelines/__init__.py
touch funding_curve/funding_curve/pipelines/ingest.py
touch funding_curve/funding_curve/pipelines/snapshot.py

# Create test files
touch funding_curve/tests/__init__.py
touch funding_curve/tests/test_builders.py

echo "Project structure created successfully!"
echo "Directory structure:"
find funding_curve -type f | sort