"""Pytest plugin — runtime call tracing via --trace-calls flag.

Usage:
    pytest --trace-calls tests/
    → generates dynamic-deps.json in the project root
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--trace-calls",
        action="store_true",
        default=False,
        help="Enable runtime call tracing (generates dynamic-deps.json)",
    )


def pytest_configure(config):
    if not config.getoption("--trace-calls", default=False):
        return
    from matrixone_graph.tracer import CallTracer

    project_root = os.environ.get("TRACE_PROJECT_ROOT", str(Path.cwd()))
    tracer = CallTracer(project_root=project_root)
    config._call_tracer = tracer
    tracer.start()


def pytest_unconfigure(config):
    tracer = getattr(config, "_call_tracer", None)
    if tracer is None:
        return
    tracer.stop()
    output = os.environ.get("TRACE_OUTPUT", "dynamic-deps.json")
    tracer.save(output)
    edge_count = len(tracer.edges)
    print(f"\n[trace-calls] Captured {edge_count} dynamic edges → {output}")
