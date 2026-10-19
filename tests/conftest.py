"""Pytest configuration and test environment initialization."""

import os
import sys

# Ensure testing flag is set before any application modules are imported
os.environ["TESTING"] = "1"
