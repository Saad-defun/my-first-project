"""Common interface every backend implements."""
from abc import ABC, abstractmethod
from collections import Counter


def summary(drawing: str, layers: dict, blocks: list[str], block_refs: Counter,
            text_styles: list[str], dim_styles: list[str], linetypes: list[str],
            by_layer: Counter, by_type: Counter) -> dict:
    """Build the drawing summary both backends return, so they compare the same way."""
    return {
        "drawing": drawing,
        "layers": dict(sorted(layers.items())),
        "blocks": {name: block_refs.get(name, 0) for name in sorted(blocks)},  # name -> references
        "text_styles": sorted(text_styles),
        "dim_styles": sorted(dim_styles),
        "linetypes": sorted(linetypes),
        "entities_by_layer": dict(sorted(by_layer.items())),
        "entities_by_type": dict(sorted(by_type.items())),
    }


class Backend(ABC):
    name: str

    @abstractmethod
    def status(self, path: str | None = None) -> dict:
        """Report whether the backend is usable."""

    @abstractmethod
    def inspect(self, path: str | None = None) -> dict:
        """Summarise a drawing (model space) in the shared summary format."""
