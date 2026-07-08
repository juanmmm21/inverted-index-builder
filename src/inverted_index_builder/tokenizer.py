"""Tokenizador Unicode-consciente, sin dependencias de NLP de terceros.

Normaliza a NFC antes de fragmentar para que dos representaciones Unicode distintas de
la misma palabra acentuada (p. ej. 'café' con la é como un único codepoint, frente a
'e' + acento combinante) no generen entradas de vocabulario separadas — ver
`../CLAUDE.md`, sección 2.D. El plegado usa `casefold()` en vez de `lower()` porque
pliega correctamente casos como la 'ß' alemana ('ss'), evitando que variantes de
mayúsculas/minúsculas de alfabetos no latinos fragmenten el vocabulario (ver
`../AGENTS.md`, Definition of Done de este proyecto).
"""

from __future__ import annotations

import re
import unicodedata

from inverted_index_builder.models import Token

# `[^\W_]+` capta secuencias de letras y dígitos Unicode: `\w` en Python ya opera sobre
# categorías Unicode por defecto (no hace falta `re.UNICODE` explícito en `str`, pero se
# deja para que la intención quede documentada en el propio patrón), y se excluye el
# guion bajo, que `\w` sí incluiría. Puntuación, espacios y símbolos actúan como
# separadores de token.
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[Token]:
    """Tokeniza `text` devolviendo tokens normalizados con su posición ordinal.

    La posición es el índice (0-based) del token dentro de la secuencia de tokens del
    propio documento, no el offset de carácter en el texto original — es lo que
    necesitan las búsquedas de frases exactas para comprobar adyacencia entre términos.
    """
    normalized_text = unicodedata.normalize("NFC", text)
    tokens: list[Token] = []
    for position, match in enumerate(_TOKEN_PATTERN.finditer(normalized_text)):
        # Re-normalizar tras el `casefold()`: el plegado de algunos caracteres (p. ej.
        # ciertas ligaduras) puede producir secuencias que NFC vuelve a componer.
        term = unicodedata.normalize("NFC", match.group(0).casefold())
        tokens.append(Token(text=term, position=position))
    return tokens
