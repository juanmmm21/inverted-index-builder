"""Interfaz que desacopla `pipeline.py` de la implementación concreta de tokenización,
para poder testear la agregación de postings con dobles de prueba deterministas,
independientes de las reglas Unicode reales de `tokenizer.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from inverted_index_builder.models import Token


@runtime_checkable
class Tokenizer(Protocol):
    """Convierte un texto en la secuencia ordenada de tokens normalizados que lo componen."""

    def __call__(self, text: str) -> list[Token]: ...
