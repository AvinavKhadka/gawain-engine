"""Unit tests for Gawain Engine.

Discovered by CI via:
    python -m unittest discover -s tests -t . -p "test_*.py"

The -t . keeps the repo root on sys.path so tests can import server/ and
config/. test_pipeline.py deliberately lives in the repo root, NOT here: it
is a manual end-to-end script that needs a live database and Ollama.
"""