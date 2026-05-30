"""Engine optimization sub-package — grid search, walk-forward."""

from engine.optimization.optimizer import (
    OptimizationResult,
    OptimizationRun,
    grid_search,
    random_search,
)
from engine.optimization.shared_arrays import SharedArrayBundle
from engine.optimization.vectorized_runner import (
    VectorizedRunner,
    make_vectorized_runner,
)

__all__ = [
    "OptimizationRun",
    "OptimizationResult",
    "grid_search",
    "random_search",
    "SharedArrayBundle",
    "VectorizedRunner",
    "make_vectorized_runner",
]
