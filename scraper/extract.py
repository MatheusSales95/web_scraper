"""Extração de informações do HTML: links dos posts, próxima página, título e links de download."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import Tag

from .config import PostConfig
from .http_client import Page

# Pontuação mínima para um link ser considerado "de download" na detecção automática.
MIN_SCORE = 2


class ContentNotFoundError(Exception):
    """O seletor da área de conteúdo não encontrou nada na página do post."""


@dataclass(frozen=True)
class PostLink:
    url: str
    title: str
    title_from_selector: bool = False  # o título veio do `post_title_selector`, e não do texto inteiro do link


@dataclass(frozen=True)
class DownloadLink:
    url: str
    filename: str = ""  # nome de arquivo indicado pelo próprio link (ex.: "Projeto Final.zip"), se houver


def extract_post_links(page: Page, selector: str, title_selector: str = "") -> list[PostLink]:
    """Links dos posts na página de listagem, na ordem em que aparecem e sem repetições.

    O título é o texto do link. Quando o link envolve o card inteiro (título, data, contagem de
    anexos...), `title_selector` aponta, dentro do elemento selecionado, só para o título.
    """
    posts: list[PostLink] = []
    seen: set[str] = set()
    for element in page.soup.select(selector):
        anchor = _as_anchor(element)
        url = absolute_url(page, anchor)
        if url and url not in seen:
            seen.add(url)
            heading = element.select_one(title_selector) if title_selector else None
            title = _clean(heading.get_text(" ")) if heading else ""
            posts.append(PostLink(url, title or _clean(anchor.get_text(" ")), title_from_selector=bool(title)))
    return posts


def find_next_page(page: Page, selector: str) -> str | None:
    """URL da próxima página da listagem, ou None se não houver."""
    element = page.soup.select_one(selector)
    return absolute_url(page, _as_anchor(element)) if element else None


def extract_title(page: Page) -> str:
    """Título do post: og:title, depois <h1>, depois <title>."""
    meta = page.soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content", "").strip():
        return _clean(meta["content"])
    for tag in ("h1", "title"):
        element = page.soup.find(tag)
        if element and element.get_text(strip=True):
            return _clean(element.get_text(" "))
    return ""


def find_download_links(page: Page, cfg: PostConfig) -> list[DownloadLink]:
    """Links de download da página do post, do mais provável para o menos provável.

    Com `download_link_selector` configurado, usa exatamente o que o seletor encontrar.
    Caso contrário, dá uma pontuação a cada link (ver `download_score`) e fica com os que
    atingirem MIN_SCORE. Devolve só o primeiro, a menos que `download_all_links` seja true.
    """
    scope: Tag = page.soup
    if cfg.content_selector:
        scope = page.soup.select_one(cfg.content_selector)
        if scope is None:
            raise ContentNotFoundError(f"o seletor {cfg.content_selector!r} não encontrou a área de conteúdo")

    if cfg.download_link_selector:
        anchors = [_as_anchor(element) for element in scope.select(cfg.download_link_selector)]
    else:
        anchors = _rank_by_score(page, scope, cfg)

    links: dict[str, DownloadLink] = {}
    for anchor in anchors:
        url = absolute_url(page, anchor)
        if url and url not in links:
            links[url] = DownloadLink(url, link_filename(anchor, cfg.extensions))
    found = list(links.values())
    return found if cfg.download_all_links else found[:1]


def _rank_by_score(page: Page, scope: Tag, cfg: PostConfig) -> list[Tag]:
    keywords = [k for k in (_normalize(k) for k in cfg.keywords) if k]
    scored = []
    for anchor in scope.find_all("a", href=True):
        url = absolute_url(page, anchor)
        if url:
            score = download_score(anchor, url, keywords, cfg.extensions)
            if score >= MIN_SCORE:
                scored.append((score, anchor))
    scored.sort(key=lambda item: item[0], reverse=True)  # sort estável: empates mantêm a ordem da página
    return [anchor for _, anchor in scored]


def download_score(anchor: Tag, url: str, keywords: list[str], extensions: tuple[str, ...]) -> int:
    """Quanto o link parece ser de download. `keywords` já devem estar normalizadas."""
    text = _normalize(" ".join([
        anchor.get_text(" "),
        anchor.get("title", ""),
        anchor.get("aria-label", ""),
        *(img.get("alt", "") for img in anchor.find_all("img")),
    ]))
    attributes = _normalize(" ".join([*anchor.get("class", []), anchor.get("id", "")]))
    path = urlsplit(url).path.lower()

    score = 0
    if anchor.has_attr("download"):
        score += 3  # atributo HTML feito justamente para downloads
    if path.endswith(extensions):
        score += 3
    if _written_filename(anchor, extensions):
        score += 3  # o texto do link é um nome de arquivo, ex.: "Projeto Final v2.zip"
    if any(k in text for k in keywords):
        score += 2
    if any(k in attributes for k in keywords):
        score += 2
    if any(k in _normalize(url) for k in keywords):
        score += 1
    return score


def link_filename(anchor: Tag, extensions: tuple[str, ...]) -> str:
    """Nome de arquivo indicado pelo link, ou '' se não houver.

    O valor do atributo `download` (ex.: <a download="Projeto Final.zip">) tem prioridade sobre o
    nome escrito no texto: é o nome que o próprio navegador usaria ao salvar.
    """
    return _clean(anchor.get("download") or "") or _written_filename(anchor, extensions)


def _written_filename(anchor: Tag, extensions: tuple[str, ...]) -> str:
    """Nome de arquivo escrito no link (no texto ou no atributo title), ou '' se não houver."""
    for text in (anchor.get_text(" "), anchor.get("title", "")):
        name = filename_in_text(text, extensions)
        if name:
            return name
    return ""


def filename_in_text(text: str, extensions: tuple[str, ...]) -> str:
    """'Projeto Final v2.zip (15 MB)' -> 'Projeto Final v2.zip'; '' se o texto não tiver um nome de arquivo."""
    if not extensions:
        return ""
    text = _clean(text)
    match = _filename_pattern(extensions).search(text)
    if not match:
        return ""
    # Se o texto do link for uma URL ou um caminho, fica só a última parte.
    return re.split(r"[\\/]", text[: match.end()])[-1].strip()


@lru_cache(maxsize=None)
def _filename_pattern(extensions: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(extension) for extension in extensions)
    # (?<=\S)  a extensão vem grudada num nome: "arquivo.zip" sim, "arquivos .zip" não
    # (...)+   aceita extensões duplas, como ".tar.gz"
    # (?!\w)   e termina ali: ".zip" sim, ".zipado" não
    return re.compile(rf"(?<=\S)(?:{alternatives})+(?!\w)", re.IGNORECASE)


def absolute_url(page: Page, anchor: Tag | None) -> str | None:
    """URL absoluta (sem #fragmento) do link, ou None se não for um link http(s) navegável."""
    if anchor is None:
        return None
    href = (anchor.get("href") or "").strip()
    if not href or href.startswith("#"):
        return None
    url = urldefrag(urljoin(page.base_url, href)).url
    return url if urlsplit(url).scheme in ("http", "https") else None


def _as_anchor(element: Tag | None) -> Tag | None:
    """Aceita seletores que apontam para o <a> ou para um elemento que contém o <a>."""
    if element is None or element.name == "a":
        return element
    return element.find("a", href=True)


def _normalize(text: str) -> str:
    """Minúsculas e sem acentos, para comparar 'Baixar' com 'baixar' e 'Música' com 'musica'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _clean(text: str) -> str:
    return " ".join(text.split())
