"""Bounded, validated JSON responses tied to an immutable dataset generation."""
from functools import lru_cache

from . import services
from .analytics import Dataset
from .schemas import Accuracy, DataQuality, Evolution, Overview

_VIEWS = {"overview": ("overview", Overview), "accuracy": ("accuracy", Accuracy),
          "evolution": ("evolution", Evolution), "data-quality": ("data_quality", DataQuality)}


@lru_cache(maxsize=128)
def _render_view(view: str, dataset: Dataset, source_mode: str, *parameters) -> str:
    """Keep date, cutoff, reveal mode, and dataset identity in every cache key.

    The response contract is validated before caching. Cached JSON strings are
    immutable, so callers cannot change another request's response in place.
    """
    if view not in _VIEWS:
        raise ValueError("Unknown analytical view")
    service_name, model = _VIEWS[view]
    result = getattr(services, service_name)(dataset, *parameters)
    validated = model.model_validate(result)
    validated.provenance.source_mode = source_mode
    return validated.model_dump_json()


def render_view(view: str, dataset: Dataset, *parameters) -> str:
    # Freshness can expire without replacing the source records. Include its
    # effective state so an old cached JSON response cannot keep claiming live.
    source_mode = services.provenance(dataset)["source_mode"]
    return _render_view(view, dataset, source_mode, *parameters)


render_view.cache_info = _render_view.cache_info


def clear_response_cache():
    """Release cached output and references to datasets replaced by a refresh."""
    _render_view.cache_clear()
    Dataset.calibration.cache_clear()
    Dataset.historical_pool.cache_clear()
    Dataset.evaluations.cache_clear()
