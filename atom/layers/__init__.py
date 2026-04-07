"""Custom layers — middleware extensions for the Atom agent."""
from atom.layers.stall_detector import StallDetector, StallDetectorMiddleware
from atom.layers.context_compaction import ContextCompactor, ContextCompactionMiddleware
from atom.layers.auto_dream import AutoDreamExtractor, AutoDreamMiddleware

__all__ = [
    "StallDetector", "StallDetectorMiddleware",
    "ContextCompactor", "ContextCompactionMiddleware",
    "AutoDreamExtractor", "AutoDreamMiddleware",
]
