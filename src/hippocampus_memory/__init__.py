"""Portable, local-first memory lifecycle primitives for AI agents."""

from .engine import MemoryEngine
from .protocol import dispatch_event

__all__ = ["MemoryEngine", "dispatch_event"]
