"""Estado persistente em SQLite (o que já foi baixado, o que falhou) e exportação dos relatórios CSV.

Guardar o estado permite interromper o scraper (Ctrl+C, queda de energia...) e continuar depois:
posts concluídos com sucesso são pulados e os que falharam são tentados de novo.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    url        TEXT PRIMARY KEY,
    title      TEXT,
    status     TEXT NOT NULL,          -- 'ok' ou 'falha'
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    url        TEXT PRIMARY KEY,       -- URL do arquivo
    post_url   TEXT NOT NULL,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    id           INTEGER PRIMARY KEY,
    page_url     TEXT NOT NULL,        -- post (ou página da listagem) onde aconteceu a falha
    title        TEXT,
    download_url TEXT,
    reason       TEXT NOT NULL,
    details      TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS failures_page_url ON failures (page_url);
"""

FAILURE_COLUMNS = ["data_hora", "titulo", "url_post", "url_download", "motivo", "detalhes"]
DOWNLOAD_COLUMNS = ["data_hora", "titulo", "url_post", "url_download", "arquivo", "tamanho_bytes"]


@dataclass(frozen=True)
class Failure:
    reason: str
    details: str
    download_url: str = ""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def is_post_done(self, url: str) -> bool:
        row = self._conn.execute("SELECT status FROM posts WHERE url = ?", (url,)).fetchone()
        return row is not None and row[0] == "ok"

    def downloaded_path(self, url: str) -> str | None:
        row = self._conn.execute("SELECT path FROM downloads WHERE url = ?", (url,)).fetchone()
        return row[0] if row else None

    def add_download(self, url: str, post_url: str, path: Path, size: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO downloads (url, post_url, path, size, created_at) VALUES (?, ?, ?, ?, ?)",
                (url, post_url, str(path), size, _now()),
            )

    def save_result(self, page_url: str, title: str, failures: Sequence[Failure], *, is_post: bool = True) -> None:
        """Troca as falhas registradas da página pelas desta tentativa e, se for um post, atualiza o status."""
        now = _now()
        with self._conn:  # tudo numa transação só
            self._conn.execute("DELETE FROM failures WHERE page_url = ?", (page_url,))
            self._conn.executemany(
                "INSERT INTO failures (page_url, title, download_url, reason, details, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(page_url, title, f.download_url, f.reason, f.details, now) for f in failures],
            )
            if is_post:
                self._conn.execute(
                    "INSERT OR REPLACE INTO posts (url, title, status, updated_at) VALUES (?, ?, ?, ?)",
                    (page_url, title, "falha" if failures else "ok", now),
                )

    def export_failures(self, csv_path: Path) -> tuple[Path, int]:
        rows = self._conn.execute(
            "SELECT created_at, title, page_url, download_url, reason, details FROM failures ORDER BY id"
        ).fetchall()
        return _write_csv(csv_path, FAILURE_COLUMNS, rows), len(rows)

    def export_downloads(self, csv_path: Path) -> tuple[Path, int]:
        rows = self._conn.execute(
            "SELECT d.created_at, COALESCE(p.title, ''), d.post_url, d.url, d.path, d.size"
            " FROM downloads d LEFT JOIN posts p ON p.url = d.post_url ORDER BY d.rowid"
        ).fetchall()
        return _write_csv(csv_path, DOWNLOAD_COLUMNS, rows), len(rows)


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> Path:
    """Grava o CSV no formato que o Excel em português abre direto (separador ';' e UTF-8 com BOM)."""
    try:
        return _write(path, header, rows)
    except PermissionError:
        # No Windows, um CSV aberto no Excel fica travado; grava com outro nome em vez de perder o relatório.
        alternative = path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        log.warning("%s está aberto em outro programa (Excel?); relatório salvo em %s", path.name, alternative.name)
        return _write(alternative, header, rows)


def _write(path: Path, header: list[str], rows: list[tuple]) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
