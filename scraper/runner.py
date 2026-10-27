"""Fluxo principal: percorre a listagem, entra em cada post, baixa o arquivo e registra as falhas.

A página principal é lida uma vez e a lista de posts dela fica guardada; depois de cada post o
scraper "volta" para essa lista e segue para o próximo, sem precisar baixar a listagem de novo.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import requests

from .config import Config
from .downloader import IncompleteDownloadError, NotAFileError, download_file, sanitize_filename
from .extract import (
    ContentNotFoundError,
    DownloadLink,
    PostLink,
    extract_post_links,
    extract_title,
    find_download_links,
    find_next_page,
)
from .http_client import BlockedByRobotsError, HttpClient, Page
from .storage import Failure, Store

log = logging.getLogger(__name__)

LISTING_TITLE = "(página da listagem)"
MAX_DETAILS_LENGTH = 500


@dataclass
class Stats:
    posts_found: int = 0
    skipped: int = 0
    ok: int = 0
    failed: int = 0
    files: int = 0
    bytes: int = 0

    @property
    def processed(self) -> int:
        return self.ok + self.failed


class Runner:
    def __init__(self, cfg: Config, client: HttpClient, store: Store, *, dry_run: bool = False, max_posts: int = 0):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.dry_run = dry_run
        self.max_posts = max_posts
        self.stats = Stats()

    def run(self) -> Stats:
        seen: set[str] = set()
        for listing in self._listing_pages():
            posts = extract_post_links(listing, self.cfg.site.post_link_selector, self.cfg.site.post_title_selector)
            log.info("%d post(s) nesta página.", len(posts))
            if not posts:
                log.warning(
                    "Nenhum post encontrado em %s. Confira o 'post_link_selector' no arquivo de configuração "
                    "(ou o site monta a página com JavaScript, o que exigiria um navegador automatizado).",
                    listing.url,
                )
            for post in posts:
                if post.url in seen:
                    continue
                if self.max_posts and self.stats.processed >= self.max_posts:
                    log.info("Limite de %d post(s) atingido (--max-posts).", self.max_posts)
                    return self.stats
                seen.add(post.url)
                self.stats.posts_found += 1
                if self.store.is_post_done(post.url):
                    self.stats.skipped += 1
                    log.debug("Já concluído em uma execução anterior, pulando: %s", post.url)
                    continue
                self._process_post(post)
        return self.stats

    def _listing_pages(self) -> Iterator[Page]:
        """Páginas da listagem, seguindo o link de "próxima página" até acabar."""
        url: str | None = self.cfg.site.start_url
        visited: set[str] = set()
        while url and url not in visited:
            if self.cfg.site.max_pages and len(visited) >= self.cfg.site.max_pages:
                log.info("Limite de %d página(s) da listagem atingido (max_pages).", self.cfg.site.max_pages)
                return
            visited.add(url)
            log.info("=== Página %d da listagem: %s", len(visited), url)
            try:
                page = self.client.get_page(url)
            except Exception as exc:
                reason, details = describe_error(exc)
                log.error(
                    "Não foi possível abrir a página da listagem (%s: %s). "
                    "Os posts dela e das páginas seguintes ficaram de fora.", reason, details,
                )
                self._save(url, LISTING_TITLE, [Failure("erro_na_listagem", f"{reason}: {details}")], is_post=False)
                return
            self._save(url, LISTING_TITLE, [], is_post=False)  # apaga falha antiga desta página, se houver
            yield page
            selector = self.cfg.site.next_page_selector
            url = find_next_page(page, selector) if selector else None

    def _process_post(self, post: PostLink) -> None:
        log.info("[%d] %s", self.stats.processed + 1, post.title or "(sem título)")
        log.info("    %s", post.url)
        title = post.title
        failures: list[Failure] = []
        links: list[DownloadLink] = []
        try:
            page = self.client.get_page(post.url)
            if not post.title_from_selector:  # o título escolhido pelo seletor da listagem tem prioridade
                title = extract_title(page) or title
            links = find_download_links(page, self.cfg.post)
        except Exception as exc:
            failures.append(self._failure(exc))
        else:
            if not links:
                failures.append(self._failure_of("link_nao_encontrado", "nenhum link de download na página do post"))

        for link in links:
            failure = self._download(link, post.url, title)
            if failure:
                failures.append(failure)

        self._save(post.url, title, failures)
        if failures:
            self.stats.failed += 1
        else:
            self.stats.ok += 1

    def _download(self, link: DownloadLink, post_url: str, title: str) -> Failure | None:
        if self.dry_run:
            name = f"  (nome no link: {link.filename})" if link.filename else ""
            log.info("    [SIMULAÇÃO] seria baixado: %s%s", link.url, name)
            return None
        previous = self.store.downloaded_path(link.url)
        if previous:
            log.info("    [JÁ BAIXADO] %s", previous)
            return None

        dest_dir = self.cfg.files.download_dir
        if self.cfg.files.subfolder_per_post:
            dest_dir = dest_dir / (sanitize_filename(title, max_length=80) or "sem-titulo")
        log.info("    baixando %s", link.url)
        try:
            result = download_file(self.client, link.url, dest_dir, fallback_name=title, link_name=link.filename)
        except Exception as exc:
            return self._failure(exc, download_url=link.url)

        self.store.add_download(link.url, post_url, result.path, result.size)
        self.stats.files += 1
        self.stats.bytes += result.size
        log.info("    [OK] %s (%s)", result.path.name, human_size(result.size))
        return None

    def _failure(self, exc: Exception, download_url: str = "") -> Failure:
        reason, details = describe_error(exc)
        if reason == "erro_inesperado":
            log.debug("Detalhes do erro inesperado:", exc_info=exc)  # traceback só no arquivo de log
        return self._failure_of(reason, details, download_url)

    def _failure_of(self, reason: str, details: str, download_url: str = "") -> Failure:
        log.info("    [FALHA] %s: %s", reason, details)
        return Failure(reason, details[:MAX_DETAILS_LENGTH], download_url)

    def _save(self, page_url: str, title: str, failures: list[Failure], *, is_post: bool = True) -> None:
        if not self.dry_run:
            self.store.save_result(page_url, title, failures, is_post=is_post)


def describe_error(exc: Exception) -> tuple[str, str]:
    """Traduz uma exceção para (motivo, detalhes), que vão para a lista de falhas."""
    if isinstance(exc, BlockedByRobotsError):
        return "bloqueado_robots_txt", str(exc)
    if isinstance(exc, ContentNotFoundError):
        return "conteudo_nao_encontrado", str(exc)
    if isinstance(exc, NotAFileError):
        return "nao_e_arquivo", str(exc)
    if isinstance(exc, IncompleteDownloadError):
        return "download_incompleto", str(exc)
    # As exceções do requests herdam de OSError, então precisam ser testadas antes dele.
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        return "erro_http", f"HTTP {resp.status_code} {resp.reason}" if resp is not None else str(exc)
    if isinstance(exc, requests.Timeout):
        return "timeout", str(exc)
    if isinstance(exc, requests.ConnectionError):
        return "erro_conexao", str(exc)
    if isinstance(exc, requests.RequestException):
        return "erro_requisicao", str(exc)
    if isinstance(exc, OSError):
        return "erro_ao_salvar", str(exc)
    return "erro_inesperado", f"{type(exc).__name__}: {exc}"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
