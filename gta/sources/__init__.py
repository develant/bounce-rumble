"""Pluggable discovery and acquisition sources for the GTA VI pipeline.

Each source module provides discovery (metadata, popularity, live status) and, where an
official method exists, acquisition of a local file. Sources are independent: a failing or
unauthorized source never blocks the others. Adding a source means adding a module here and
registering it in ingest.py.
"""
