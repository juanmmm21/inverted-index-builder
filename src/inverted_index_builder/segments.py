"""Índice segmentado incremental: segmentos inmutables, borrados lógicos
(tombstones / live docs) y fusiones en segundo plano.

## Por qué segmentos

Reconstruir el índice completo cada vez que cambia una página no escala: el
coste es proporcional al corpus, no al cambio. Aquí cada lote de documentos
nuevos o actualizados se escribe como un **segmento** nuevo — un directorio
con el mismo formato v1 de `serialization.py` (cualquier lector existente
puede abrir un segmento suelto) — y nunca se reescribe después. Un documento
actualizado no se modifica en su segmento: se marca como borrado allí (su
`doc_id` pasa a la lista de tombstones de ese segmento) y su nueva versión
entra con un `doc_id` nuevo en el segmento nuevo. Las fusiones juntan varios
segmentos en uno, descartando de verdad los documentos borrados.

## Contrato en disco (`segments.json`, formato segmentado v1)

```text
<root>/
├── segments.json            # manifiesto: generación + lista de segmentos vivos
├── seg-000001/              # segmento inmutable, formato v1 de serialization.py
├── seg-000002/
└── deletes/
    └── seg-000001-g000007.json   # tombstones de seg-000001 en la generación 7
```

- Los `doc_id` son **globales y estables**: un segmento nuevo recibe IDs a
  partir de `next_doc_id`, y una fusión conserva los IDs de los documentos
  que sobreviven. Así una señal externa indexada por `doc_id` (PageRank,
  snippets) no se invalida por una fusión. Consecuencia: dentro de un
  segmento los IDs son ascendentes pero pueden tener huecos.
- Cada commit escribe primero los ficheros nuevos (segmento, tombstones con
  el número de generación en el nombre) y después reemplaza `segments.json`
  de forma atómica (`os.replace`). Un lector que abre el manifiesto ve
  siempre un estado completo; un proceso que muere a mitad deja como mucho
  ficheros huérfanos, que se borran al reabrir el índice.
- Un único escritor por directorio: el cerrojo es de proceso
  (`threading.Lock`), no de sistema de ficheros. El fusionado en segundo plano
  (`BackgroundMerger`) comparte ese cerrojo con las altas y bajas.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from inverted_index_builder.models import (
    DocumentRecord,
    IndexStats,
    InvertedIndex,
    Posting,
    PostingsList,
)
from inverted_index_builder.pipeline import IndexBuilder
from inverted_index_builder.serialization import read_index, write_index

logger = logging.getLogger(__name__)

SEGMENTS_MANIFEST_FILENAME: Final[str] = "segments.json"
SEGMENTED_FORMAT: Final[str] = "beacon-segmented-index"
SEGMENTED_FORMAT_VERSION: Final[int] = 1
_DELETES_DIRNAME: Final[str] = "deletes"
_SEGMENT_PREFIX: Final[str] = "seg-"


class SegmentedIndexError(RuntimeError):
    """Manifiesto ausente, de otra versión o inconsistente con los ficheros."""


@dataclass(frozen=True, slots=True)
class SegmentInfo:
    name: str
    doc_count: int
    """Documentos escritos en el segmento (vivos + borrados)."""
    deleted_count: int
    deletes_file: str | None
    """Ruta relativa a la raíz del fichero de tombstones vigente, o `None`."""

    @property
    def live_count(self) -> int:
        return self.doc_count - self.deleted_count

    @property
    def deleted_ratio(self) -> float:
        return self.deleted_count / self.doc_count if self.doc_count else 0.0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "doc_count": self.doc_count,
            "deleted_count": self.deleted_count,
            "deletes_file": self.deletes_file,
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> SegmentInfo:
        deletes_file = data.get("deletes_file")
        return SegmentInfo(
            name=str(data["name"]),
            doc_count=int(data["doc_count"]),
            deleted_count=int(data["deleted_count"]),
            deletes_file=str(deletes_file) if deletes_file is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SegmentsManifest:
    generation: int
    next_doc_id: int
    next_segment_number: int
    segments: tuple[SegmentInfo, ...]

    @property
    def live_documents(self) -> int:
        return sum(segment.live_count for segment in self.segments)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "format": SEGMENTED_FORMAT,
            "format_version": SEGMENTED_FORMAT_VERSION,
            "generation": self.generation,
            "next_doc_id": self.next_doc_id,
            "next_segment_number": self.next_segment_number,
            "segments": [segment.to_json_dict() for segment in self.segments],
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> SegmentsManifest:
        if data.get("format") != SEGMENTED_FORMAT:
            raise SegmentedIndexError(f"no es un índice segmentado: format={data.get('format')!r}")
        version = int(data.get("format_version", -1))
        if version != SEGMENTED_FORMAT_VERSION:
            raise SegmentedIndexError(
                f"formato segmentado no soportado: version {version}, "
                f"esperado {SEGMENTED_FORMAT_VERSION}"
            )
        return SegmentsManifest(
            generation=int(data["generation"]),
            next_doc_id=int(data["next_doc_id"]),
            next_segment_number=int(data["next_segment_number"]),
            segments=tuple(SegmentInfo.from_json_dict(entry) for entry in data["segments"]),
        )

    @staticmethod
    def load(root: Path) -> SegmentsManifest:
        path = root / SEGMENTS_MANIFEST_FILENAME
        if not path.exists():
            raise SegmentedIndexError(f"no existe {path}: ¿es un índice segmentado?")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SegmentedIndexError(f"{path} no es JSON válido: {error}") from error
        return SegmentsManifest.from_json_dict(data)


def is_segmented_index(path: Path) -> bool:
    return (path / SEGMENTS_MANIFEST_FILENAME).exists()


def read_deleted_doc_ids(root: Path, segment: SegmentInfo) -> frozenset[int]:
    if segment.deletes_file is None:
        return frozenset()
    path = root / segment.deletes_file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SegmentedIndexError(f"no se pudieron leer los tombstones {path}: {error}") from error
    return frozenset(int(doc_id) for doc_id in data["deleted_doc_ids"])


@dataclass(frozen=True, slots=True)
class MergeReport:
    merged: tuple[str, ...]
    into: str | None
    """Segmento resultante, o `None` si no quedaba ningún documento vivo."""
    live_documents: int
    expunged_documents: int


@dataclass(frozen=True, slots=True)
class TieredMergePolicy:
    """Política de fusión por niveles (como la `TieredMergePolicy` de Lucene,
    simplificada): los segmentos se agrupan por nivel de tamaño
    (`floor(log_merge_factor(docs vivos))`) y, cuando un nivel acumula
    `merge_factor` segmentos, se fusionan los más antiguos de ese nivel. Así
    cada documento se reescribe O(log N) veces en toda su vida, no una vez por
    lote. Aparte, un segmento con más de `max_deleted_ratio` de borrados se
    reescribe solo para purgarlos: de lo contrario seguiría ocupando disco y
    tiempo de lectura por documentos que ninguna búsqueda puede devolver."""

    merge_factor: int = 4
    max_deleted_ratio: float = 0.3

    def __post_init__(self) -> None:
        if self.merge_factor < 2:
            raise ValueError(f"merge_factor debe ser >= 2, recibido {self.merge_factor}")
        if not 0.0 < self.max_deleted_ratio <= 1.0:
            raise ValueError(
                f"max_deleted_ratio debe estar en (0, 1], recibido {self.max_deleted_ratio}"
            )

    def _tier(self, segment: SegmentInfo) -> int:
        return int(math.log(max(1, segment.live_count), self.merge_factor))

    def find_merge(self, manifest: SegmentsManifest) -> tuple[str, ...] | None:
        """Siguiente fusión a hacer, o `None`. Determinista: los segmentos se
        recorren en el orden del manifiesto (el de creación)."""
        tiers: dict[int, list[str]] = {}
        for segment in manifest.segments:
            tiers.setdefault(self._tier(segment), []).append(segment.name)
        for tier in sorted(tiers):
            names = tiers[tier]
            if len(names) >= self.merge_factor:
                return tuple(names[: self.merge_factor])
        for segment in manifest.segments:
            if segment.deleted_ratio > self.max_deleted_ratio:
                return (segment.name,)
        return None


@dataclass(slots=True)
class _LiveDocument:
    url: str
    segment: str


@dataclass(slots=True)
class _State:
    manifest: SegmentsManifest
    deleted: dict[str, frozenset[int]]
    live_by_url: dict[str, int]
    live_documents: dict[int, _LiveDocument] = field(default_factory=dict)


class SegmentedIndex:
    """Escritor del índice segmentado. Ver el docstring del módulo para el
    contrato en disco. Thread-safe: altas, bajas y fusiones se serializan con
    un único cerrojo, salvo la construcción del segmento fusionado, que ocurre
    fuera del cerrojo para no bloquear las altas durante una fusión larga."""

    def __init__(self, root: Path, state: _State, builder: IndexBuilder) -> None:
        self._root = root
        self._state = state
        self._builder = builder
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def manifest(self) -> SegmentsManifest:
        with self._lock:
            return self._state.manifest

    @classmethod
    def create(cls, root: Path, builder: IndexBuilder | None = None) -> SegmentedIndex:
        if is_segmented_index(root):
            raise SegmentedIndexError(f"ya existe un índice segmentado en {root}")
        root.mkdir(parents=True, exist_ok=True)
        manifest = SegmentsManifest(generation=0, next_doc_id=0, next_segment_number=1, segments=())
        _write_manifest_atomically(root, manifest)
        return cls(
            root, _State(manifest=manifest, deleted={}, live_by_url={}), builder or IndexBuilder()
        )

    @classmethod
    def open(cls, root: Path, builder: IndexBuilder | None = None) -> SegmentedIndex:
        manifest = SegmentsManifest.load(root)
        state = _State(manifest=manifest, deleted={}, live_by_url={})
        for segment in manifest.segments:
            deleted = read_deleted_doc_ids(root, segment)
            state.deleted[segment.name] = deleted
            for doc_id, url in _iter_segment_urls(root / segment.name):
                if doc_id in deleted:
                    continue
                state.live_by_url[url] = doc_id
                state.live_documents[doc_id] = _LiveDocument(url=url, segment=segment.name)
        index = cls(root, state, builder or IndexBuilder())
        index._remove_orphans()
        return index

    @classmethod
    def open_or_create(cls, root: Path, builder: IndexBuilder | None = None) -> SegmentedIndex:
        return cls.open(root, builder) if is_segmented_index(root) else cls.create(root, builder)

    def live_doc_id(self, url: str) -> int | None:
        with self._lock:
            return self._state.live_by_url.get(url)

    def add_documents(self, records: Iterable[dict[str, Any]]) -> list[int]:
        """Escribe `records` (formato de `html-content-extractor`) como un
        segmento nuevo. Un `url` que ya estaba vivo se trata como
        actualización: la versión anterior queda marcada como borrada en su
        segmento. Si el mismo `url` aparece varias veces en el lote, gana la
        última aparición. Devuelve los `doc_id` asignados, en el orden del
        lote ya deduplicado."""
        latest_by_url: dict[str, dict[str, Any]] = {}
        for record in records:
            latest_by_url.pop(str(record["url"]), None)
            latest_by_url[str(record["url"])] = record
        batch = list(latest_by_url.values())
        if not batch:
            return []

        with self._lock:
            manifest = self._state.manifest
            first_doc_id = manifest.next_doc_id
            name = _segment_name(manifest.next_segment_number)
            index = self._builder.build_from_records(batch, first_doc_id=first_doc_id)
            write_index(index, self._root / name)

            to_delete: dict[str, set[int]] = {}
            for record in batch:
                previous = self._state.live_by_url.get(str(record["url"]))
                if previous is not None:
                    segment = self._state.live_documents[previous].segment
                    to_delete.setdefault(segment, set()).add(previous)

            new_segment = SegmentInfo(
                name=name, doc_count=len(batch), deleted_count=0, deletes_file=None
            )
            next_manifest = replace(
                manifest,
                next_doc_id=first_doc_id + len(batch),
                next_segment_number=manifest.next_segment_number + 1,
                segments=(*manifest.segments, new_segment),
            )
            self._commit(next_manifest, to_delete)

            doc_ids = list(range(first_doc_id, first_doc_id + len(batch)))
            for doc_id, record in zip(doc_ids, batch, strict=True):
                url = str(record["url"])
                self._state.live_by_url[url] = doc_id
                self._state.live_documents[doc_id] = _LiveDocument(url=url, segment=name)
            self._state.deleted[name] = frozenset()
            return doc_ids

    def delete_urls(self, urls: Iterable[str]) -> int:
        """Marca como borrada la versión viva de cada `url`. Un `url` que no
        está vivo se ignora (borrar dos veces es idempotente). Devuelve
        cuántos documentos se borraron."""
        with self._lock:
            to_delete: dict[str, set[int]] = {}
            for url in urls:
                doc_id = self._state.live_by_url.get(url)
                if doc_id is not None:
                    to_delete.setdefault(self._state.live_documents[doc_id].segment, set()).add(
                        doc_id
                    )
            if not to_delete:
                return 0
            self._commit(self._state.manifest, to_delete)
            return sum(len(doc_ids) for doc_ids in to_delete.values())

    def merge(self, names: Sequence[str]) -> MergeReport:
        """Fusiona los segmentos `names` en uno solo con sus documentos vivos.
        La lectura y escritura del segmento nuevo ocurre fuera del cerrojo; un
        borrado que llegue a uno de los segmentos de origen mientras tanto se
        traslada al segmento fusionado en el commit, así que no se pierde."""
        if not names:
            raise ValueError("merge necesita al menos un segmento")
        with self._lock:
            known = {segment.name: segment for segment in self._state.manifest.segments}
            missing = [name for name in names if name not in known]
            if missing:
                raise SegmentedIndexError(f"segmentos inexistentes: {missing}")
            snapshot_deleted = {name: self._state.deleted.get(name, frozenset()) for name in names}
            merged_name = _segment_name(self._state.manifest.next_segment_number)
            # Se reserva el número en memoria; el siguiente commit (de esta
            # fusión o de un alta concurrente) lo persiste.
            self._state.manifest = replace(
                self._state.manifest,
                next_segment_number=self._state.manifest.next_segment_number + 1,
            )

        merged_index = _merge_segment_indexes(
            [(self._root / name, snapshot_deleted[name]) for name in names]
        )
        live_documents = merged_index.stats.total_documents
        expunged = sum(len(deleted) for deleted in snapshot_deleted.values())
        if live_documents:
            write_index(merged_index, self._root / merged_name)

        with self._lock:
            manifest = self._state.manifest
            late_deletes: set[int] = set()
            for name in names:
                late_deletes |= self._state.deleted.get(name, frozenset()) - snapshot_deleted[name]

            remaining = [segment for segment in manifest.segments if segment.name not in names]
            position = min(
                index for index, segment in enumerate(manifest.segments) if segment.name in names
            )
            new_segments = list(remaining)
            to_delete: dict[str, set[int]] = {}
            if live_documents:
                new_segments.insert(
                    position,
                    SegmentInfo(
                        name=merged_name,
                        doc_count=live_documents,
                        deleted_count=0,
                        deletes_file=None,
                    ),
                )
                if late_deletes:
                    to_delete[merged_name] = late_deletes
                self._state.deleted[merged_name] = frozenset()
            obsolete_dirs = [self._root / name for name in names]
            self._commit(replace(manifest, segments=tuple(new_segments)), to_delete, removed=names)
            for doc_id in merged_index.documents:
                live = self._state.live_documents.get(doc_id)
                if live is not None:
                    live.segment = merged_name
            for name in names:
                self._state.deleted.pop(name, None)

        for directory in obsolete_dirs:
            shutil.rmtree(directory, ignore_errors=False)
        return MergeReport(
            merged=tuple(names),
            into=merged_name if live_documents else None,
            live_documents=live_documents,
            expunged_documents=expunged,
        )

    def maybe_merge(self, policy: TieredMergePolicy) -> list[MergeReport]:
        """Aplica `policy` hasta que no proponga más fusiones."""
        reports: list[MergeReport] = []
        while (names := policy.find_merge(self.manifest)) is not None:
            reports.append(self.merge(names))
        return reports

    def compact(self, output_dir: Path) -> InvertedIndex:
        """Escribe en `output_dir` un índice v1 clásico con todos los
        documentos vivos — la vía de compatibilidad para cualquier consumidor
        que solo entienda el formato de un único directorio. Conserva los
        `doc_id` globales (puede haber huecos)."""
        with self._lock:
            sources = [
                (self._root / segment.name, self._state.deleted.get(segment.name, frozenset()))
                for segment in self._state.manifest.segments
            ]
        index = _merge_segment_indexes(sources)
        write_index(index, output_dir)
        return index

    def _commit(
        self,
        manifest: SegmentsManifest,
        to_delete: dict[str, set[int]],
        *,
        removed: Sequence[str] = (),
    ) -> None:
        """Debe llamarse con el cerrojo tomado. Escribe los tombstones nuevos,
        reemplaza el manifiesto de forma atómica y solo entonces borra los
        ficheros de tombstones que han dejado de estar referenciados."""
        generation = manifest.generation + 1
        obsolete_files: list[Path] = []
        segments: list[SegmentInfo] = []
        for segment in manifest.segments:
            extra = to_delete.get(segment.name)
            if not extra:
                segments.append(segment)
                continue
            deleted = self._state.deleted.get(segment.name, frozenset()) | frozenset(extra)
            relative = f"{_DELETES_DIRNAME}/{segment.name}-g{generation:06d}.json"
            path = self._root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"segment": segment.name, "deleted_doc_ids": sorted(deleted)}),
                encoding="utf-8",
            )
            if segment.deletes_file is not None:
                obsolete_files.append(self._root / segment.deletes_file)
            self._state.deleted[segment.name] = deleted
            segments.append(replace(segment, deleted_count=len(deleted), deletes_file=relative))
            for doc_id in extra:
                live = self._state.live_documents.pop(doc_id, None)
                if live is not None and self._state.live_by_url.get(live.url) == doc_id:
                    del self._state.live_by_url[live.url]

        previous = {segment.name: segment for segment in self._state.manifest.segments}
        for name in removed:
            info = previous.get(name)
            if info is not None and info.deletes_file is not None:
                obsolete_files.append(self._root / info.deletes_file)

        next_manifest = replace(manifest, generation=generation, segments=tuple(segments))
        _write_manifest_atomically(self._root, next_manifest)
        self._state.manifest = next_manifest
        for path in obsolete_files:
            path.unlink(missing_ok=True)

    def _remove_orphans(self) -> None:
        """Borra segmentos y tombstones que ningún manifiesto referencia: restos
        de un proceso que murió entre escribir ficheros y hacer commit."""
        manifest = self._state.manifest
        referenced_dirs = {segment.name for segment in manifest.segments}
        referenced_files = {
            segment.deletes_file for segment in manifest.segments if segment.deletes_file
        }
        for entry in self._root.iterdir():
            is_segment = entry.is_dir() and entry.name.startswith(_SEGMENT_PREFIX)
            if is_segment and entry.name not in referenced_dirs:
                logger.warning("borrando segmento huérfano %s", entry)
                shutil.rmtree(entry)
        deletes_dir = self._root / _DELETES_DIRNAME
        if deletes_dir.exists():
            for entry in deletes_dir.iterdir():
                if f"{_DELETES_DIRNAME}/{entry.name}" not in referenced_files:
                    logger.warning("borrando tombstones huérfanos %s", entry)
                    entry.unlink()


class BackgroundMerger:
    """Hilo que aplica una `TieredMergePolicy` periódicamente (o en cuanto se
    llama a `trigger`). Un fallo de fusión se registra con traza completa y se
    expone en `last_error`; el hilo sigue vivo para reintentar en la
    siguiente vuelta en vez de morir en silencio."""

    def __init__(
        self,
        index: SegmentedIndex,
        policy: TieredMergePolicy | None = None,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds debe ser positivo, recibido {interval_seconds}")
        self._index = index
        self._policy = policy or TieredMergePolicy()
        self._interval = interval_seconds
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._reports: list[MergeReport] = []
        self._reports_lock = threading.Lock()
        self.last_error: BaseException | None = None

    def __enter__(self) -> BackgroundMerger:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    @property
    def reports(self) -> list[MergeReport]:
        with self._reports_lock:
            return list(self._reports)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("el merger ya está arrancado")
        self._thread = threading.Thread(target=self._run, name="segment-merger", daemon=True)
        self._thread.start()

    def trigger(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        """Termina la fusión en curso (si la hay) y para el hilo."""
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stopping.is_set():
                break
            try:
                reports = self._index.maybe_merge(self._policy)
            except Exception as error:
                # Se registra con traza y se expone en `last_error`; el hilo sigue.
                logger.exception("fallo en la fusión de segmentos de %s", self._index.root)
                self.last_error = error
                continue
            if reports:
                with self._reports_lock:
                    self._reports.extend(reports)


def _segment_name(number: int) -> str:
    return f"{_SEGMENT_PREFIX}{number:06d}"


def _write_manifest_atomically(root: Path, manifest: SegmentsManifest) -> None:
    path = root / SEGMENTS_MANIFEST_FILENAME
    temporary = root / f".{SEGMENTS_MANIFEST_FILENAME}.tmp"
    with temporary.open("w", encoding="utf-8") as output:
        output.write(json.dumps(manifest.to_json_dict(), indent=2))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _iter_segment_urls(segment_dir: Path) -> Iterator[tuple[int, str]]:
    manifest = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
    documents_path = segment_dir / str(manifest["documents_file"])
    with documents_path.open("r", encoding="utf-8") as documents_file:
        for line in documents_file:
            if line.strip():
                data = json.loads(line)
                yield int(data["doc_id"]), str(data["url"])


def _merge_segment_indexes(sources: Sequence[tuple[Path, frozenset[int]]]) -> InvertedIndex:
    """Un único `InvertedIndex` con los documentos vivos de `sources`,
    conservando sus `doc_id`. Los postings de cada término se reordenan por
    `doc_id` ascendente (contrato de `PostingsList`)."""
    documents: dict[int, DocumentRecord] = {}
    postings_by_term: dict[str, list[Posting]] = {}
    for segment_dir, deleted in sources:
        segment = read_index(segment_dir)
        for doc_id, record in segment.documents.items():
            if doc_id not in deleted:
                documents[doc_id] = record
        for term, postings_list in segment.postings_lists.items():
            live = [posting for posting in postings_list.postings if posting.doc_id not in deleted]
            if live:
                postings_by_term.setdefault(term, []).extend(live)

    postings_lists: dict[str, PostingsList] = {}
    for term, postings in postings_by_term.items():
        postings.sort(key=lambda posting: posting.doc_id)
        postings_lists[term] = PostingsList(
            term=term, document_frequency=len(postings), postings=tuple(postings)
        )
    total_documents = len(documents)
    stats = IndexStats(
        total_documents=total_documents,
        vocabulary_size=len(postings_lists),
        total_postings=sum(
            len(postings_list.postings) for postings_list in postings_lists.values()
        ),
        average_document_length=(
            sum(record.length for record in documents.values()) / total_documents
            if total_documents
            else 0.0
        ),
    )
    return InvertedIndex(documents=documents, postings_lists=postings_lists, stats=stats)
