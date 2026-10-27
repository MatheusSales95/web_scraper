"""Download de arquivos: grava em disco aos poucos, escolhe um nome seguro e tenta de novo se a conexão cair."""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from email.message import Message
from itertools import count
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import requests
from tqdm import tqdm

from .http_client import HttpClient

log = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
RETRY_PAUSE = 2.0  # segundos antes de tentar de novo (multiplicado pelo número da tentativa)

# Tipos de conteúdo que indicam uma página, e não um arquivo.
HTML_TYPES = ("text/html", "application/xhtml+xml")

# Extensões de páginas dinâmicas: "download.php" não é um bom nome para um .zip.
SCRIPT_EXTENSIONS = (".php", ".asp", ".aspx", ".jsp", ".cgi", ".html", ".htm")

# Tipos comuns em sites de download que o módulo mimetypes não conhece em todos os sistemas.
mimetypes.add_type("application/vnd.rar", ".rar")
mimetypes.add_type("application/x-rar-compressed", ".rar")
mimetypes.add_type("application/x-7z-compressed", ".7z")

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class NotAFileError(Exception):
    """O link de download abriu uma página HTML em vez de um arquivo."""


class IncompleteDownloadError(Exception):
    """A conexão caiu no meio do download ou o arquivo veio menor que o esperado."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int


def download_file(
    client: HttpClient, url: str, dest_dir: Path, fallback_name: str, link_name: str = ""
) -> DownloadResult:
    """Baixa `url` para `dest_dir`.

    `link_name` é o nome de arquivo escrito no link, se houver; `fallback_name` (o título do post)
    só é usado quando nem o servidor, nem o link, nem a URL indicam um nome.
    """
    retries = client.cfg.max_retries
    for attempt in range(retries + 1):
        try:
            return _download_once(client, url, dest_dir, fallback_name, link_name)
        except IncompleteDownloadError as e:
            if attempt == retries:
                raise
            log.info("    download interrompido (%s); tentando de novo (%d/%d)...", e, attempt + 1, retries)
            time.sleep(RETRY_PAUSE * (attempt + 1))


def _download_once(
    client: HttpClient, url: str, dest_dir: Path, fallback_name: str, link_name: str
) -> DownloadResult:
    with client.open_stream(url) as resp:
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type in HTML_TYPES:
            raise NotAFileError(f"o link abriu uma página HTML ({resp.url}), não um arquivo")

        dest_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(dest_dir / choose_filename(resp, content_type, fallback_name, link_name))
        partial = target.with_name(target.name + ".part")  # só vira o arquivo final se chegar inteiro
        expected = _expected_size(resp)
        written = 0
        try:
            with open(partial, "wb") as f, _progress_bar(target.name, expected) as bar:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    f.write(chunk)
                    written += len(chunk)
                    bar.update(len(chunk))
        except (requests.exceptions.ChunkedEncodingError, requests.ConnectionError) as e:
            partial.unlink(missing_ok=True)
            log.debug("Conexão interrompida baixando %s: %r", url, e)
            raise IncompleteDownloadError("a conexão caiu antes de o arquivo chegar inteiro") from e
        except BaseException:  # inclusive Ctrl+C: não deixa arquivo pela metade para trás
            partial.unlink(missing_ok=True)
            raise

        if expected is not None and written != expected:
            partial.unlink(missing_ok=True)
            raise IncompleteDownloadError(f"recebidos {written} de {expected} bytes")
        partial.replace(target)
    return DownloadResult(target, written)


def choose_filename(resp: requests.Response, content_type: str, fallback_name: str, link_name: str = "") -> str:
    """Nome do arquivo, nesta ordem de preferência: o que o servidor indicar (Content-Disposition),
    o nome escrito no link, o fim da URL e, por último, o título do post com a extensão do tipo de conteúdo.
    """
    name = sanitize_filename(filename_from_disposition(resp.headers.get("Content-Disposition", "")))
    if not name:
        name = sanitize_filename(link_name)
    if not name:
        last_segment = unquote(PurePosixPath(urlsplit(resp.url).path).name)
        extension = os.path.splitext(last_segment)[1].lower()
        if extension and extension not in SCRIPT_EXTENSIONS:
            name = sanitize_filename(last_segment)
    if not name:
        extension = (mimetypes.guess_extension(content_type) or "") if content_type else ""
        name = (sanitize_filename(fallback_name) or "download") + extension
    return name


def filename_from_disposition(header: str) -> str:
    """Extrai o nome de 'attachment; filename="x.zip"' (inclusive no formato filename*=UTF-8''...)."""
    if not header:
        return ""
    message = Message()
    message["Content-Disposition"] = header
    name = message.get_filename() or ""
    # Muitos servidores mandam o nome em UTF-8 "cru", que chega aqui como Latin-1 ("relatÃ³rio").
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Deixa o nome válido no Windows e impede nomes como '../../arquivo' de sair da pasta."""
    name = _INVALID_CHARS.sub("_", name)
    name = " ".join(name.split()).strip(". ")
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > max_length:
        stem, extension = os.path.splitext(name)
        extension = extension if len(extension) <= 10 else ""
        name = stem[: max_length - len(extension)].rstrip(". ") + extension
    return name


def unique_path(path: Path) -> Path:
    """Se o arquivo já existir, acrescenta ' (1)', ' (2)'... ao nome."""
    if not path.exists():
        return path
    for i in count(1):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate


def _expected_size(resp: requests.Response) -> int | None:
    if resp.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        return None  # com compressão, o Content-Length é do conteúdo comprimido
    try:
        return int(resp.headers["Content-Length"])
    except (KeyError, ValueError):
        return None


def _progress_bar(name: str, total: int | None) -> tqdm:
    # disable=None: a barra só aparece quando a saída é um terminal de verdade.
    return tqdm(
        total=total, desc=f"    {name[:40]}", unit="B", unit_scale=True, unit_divisor=1024,
        leave=False, disable=None, dynamic_ncols=True,
    )
