"""Custom layers — middleware extensions for the Totoro agent."""
from totoro.layers.stall_detector import StallDetector, StallDetectorMiddleware
from totoro.layers.context_compaction import ContextCompactor, ContextCompactionMiddleware
from totoro.layers.auto_dream import AutoDreamExtractor, AutoDreamMiddleware

__all__ = [
    "StallDetector", "StallDetectorMiddleware",
    "ContextCompactor", "ContextCompactionMiddleware",
    "AutoDreamExtractor", "AutoDreamMiddleware",
]
