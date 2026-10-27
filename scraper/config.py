"""Leitura e validação do arquivo de configuração (TOML)."""

from __future__ import annotations

import tomllib
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, get_type_hints
from urllib.parse import urlsplit

import soupsieve


class ConfigError(Exception):
    """Problema no arquivo de configuração (mensagem pronta para mostrar ao usuário)."""


DEFAULT_KEYWORDS = ("download", "baixar", "baixe", "descarregar")

DEFAULT_EXTENSIONS = (
    # compactados
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    # documentos e e-books
    ".pdf", ".epub", ".mobi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    # áudio e vídeo
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".mp4", ".mkv", ".avi", ".mov", ".webm",
    # imagens de disco
    ".iso",
)


@dataclass(frozen=True)
class SiteConfig:
    start_url: str
    post_link_selector: str
    post_title_selector: str = ""
    next_page_selector: str = ""
    max_pages: int = 0


@dataclass(frozen=True)
class PostConfig:
    content_selector: str = ""
    download_link_selector: str = ""
    download_all_links: bool = False
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS


@dataclass(frozen=True)
class FilesConfig:
    download_dir: Path = Path("downloads")
    output_dir: Path = Path("output")
    subfolder_per_post: bool = False


@dataclass(frozen=True)
class HttpConfig:
    user_agent: str = "POCScraper/0.1 (projeto de estudo)"
    timeout: float = 30.0
    delay: float = 1.0
    max_retries: int = 3
    respect_robots_txt: bool = True


@dataclass(frozen=True)
class Config:
    site: SiteConfig
    post: PostConfig = field(default_factory=PostConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    http: HttpConfig = field(default_factory=HttpConfig)


_SECTIONS = {"site": SiteConfig, "post": PostConfig, "files": FilesConfig, "http": HttpConfig}

_TYPE_NAMES = {
    bool: "true ou false",
    int: "um número inteiro",
    float: "um número",
    str: "um texto entre aspas",
    Path: "um caminho entre aspas",
}


def load_config(path: Path) -> Config:
    """Lê o TOML, valida e devolve a configuração com os caminhos já resolvidos.

    Caminhos relativos (pastas de download/saída) são relativos à pasta do arquivo de configuração.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"arquivo não encontrado: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML inválido em {path}: {e}") from None

    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"seção(ões) desconhecida(s): {', '.join(sorted(unknown))}")
    if "site" not in raw:
        raise ConfigError("a seção [site] é obrigatória")

    cfg = Config(**{name: _build(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()})
    _validate(cfg)
    return _finalize(cfg, path.resolve().parent)


def _build(cls: type, data: Any, section: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] deveria ser uma seção")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}

    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"[{section}] chave(s) desconhecida(s): {', '.join(sorted(unknown))}")
    missing = [f.name for f in fields(cls) if f.default is MISSING and f.name not in data]
    if missing:
        raise ConfigError(f"[{section}] chave(s) obrigatória(s) ausente(s): {', '.join(missing)}")

    return cls(**{key: _convert(value, hints[key], f"{section}.{key}") for key, value in data.items()})


def _convert(value: Any, expected: Any, name: str) -> Any:
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected in (str, Path):
        ok = isinstance(value, str)
    else:  # tuple[str, ...]
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        if not ok:
            raise ConfigError(f"{name} deveria ser uma lista de textos, ex.: [\"a\", \"b\"]")
        return tuple(value)

    if not ok:
        raise ConfigError(f"{name} deveria ser {_TYPE_NAMES[expected]} (veio {value!r})")
    if expected is float:
        return float(value)
    if expected is Path:
        return Path(value)
    return value


def _validate(cfg: Config) -> None:
    url = urlsplit(cfg.site.start_url)
    if url.scheme not in ("http", "https") or not url.netloc:
        raise ConfigError(f"site.start_url precisa ser uma URL completa (http/https): {cfg.site.start_url!r}")

    selectors = {
        "site.post_link_selector": cfg.site.post_link_selector,
        "site.post_title_selector": cfg.site.post_title_selector,
        "site.next_page_selector": cfg.site.next_page_selector,
        "post.content_selector": cfg.post.content_selector,
        "post.download_link_selector": cfg.post.download_link_selector,
    }
    if not cfg.site.post_link_selector.strip():
        raise ConfigError("site.post_link_selector não pode ficar vazio")
    for name, selector in selectors.items():
        if selector:
            try:
                soupsieve.compile(selector)
            except soupsieve.SelectorSyntaxError as e:
                reason = str(e).splitlines()[0]
                raise ConfigError(f"{name}: seletor CSS inválido {selector!r} ({reason})") from None

    if cfg.site.max_pages < 0:
        raise ConfigError("site.max_pages não pode ser negativo")
    if cfg.http.max_retries < 0:
        raise ConfigError("http.max_retries não pode ser negativo")
    if cfg.http.delay < 0:
        raise ConfigError("http.delay não pode ser negativo")
    if cfg.http.timeout <= 0:
        raise ConfigError("http.timeout precisa ser maior que zero")


def _finalize(cfg: Config, base_dir: Path) -> Config:
    extensions = tuple(e.lower() if e.startswith(".") else f".{e.lower()}" for e in cfg.post.extensions)
    files = replace(
        cfg.files,
        download_dir=base_dir / cfg.files.download_dir,
        output_dir=base_dir / cfg.files.output_dir,
    )
    return replace(cfg, post=replace(cfg.post, extensions=extensions), files=files)
