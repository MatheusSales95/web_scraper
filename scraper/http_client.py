"""Cliente HTTP: sessão com retentativas, pausa entre requisições e respeito ao robots.txt."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import HttpConfig

log = logging.getLogger(__name__)

# Status que valem uma nova tentativa (excesso de requisições e erros temporários do servidor).
RETRY_STATUS = (429, 500, 502, 503, 504)


class BlockedByRobotsError(Exception):
    """A URL é proibida pelo robots.txt do site."""


@dataclass
class Page:
    """Página HTML já baixada e interpretada."""

    url: str  # URL final, depois de eventuais redirecionamentos
    soup: BeautifulSoup

    @cached_property
    def base_url(self) -> str:
        """URL usada para resolver links relativos (considera a tag <base href>, se houver)."""
        base = self.soup.find("base", href=True)
        return urljoin(self.url, base["href"]) if base else self.url


class HttpClient:
    """Faz todas as requisições do scraper, uma de cada vez, com uma pausa mínima entre elas."""

    def __init__(self, cfg: HttpConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.user_agent
        retry = Retry(
            total=cfg.max_retries,
            backoff_factor=1.0,  # espera 0s, 2s, 4s... entre as tentativas
            status_forcelist=RETRY_STATUS,
            raise_on_status=False,  # esgotadas as tentativas, devolve a resposta e raise_for_status() decide
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self._last_request = float("-inf")
        self._robots: dict[str, RobotFileParser] = {}

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.session.close()

    def get_page(self, url: str) -> Page:
        """Baixa uma página HTML. Lança requests.HTTPError em respostas 4xx/5xx."""
        with self._get(url) as resp:
            resp.raise_for_status()
            # Sem charset no cabeçalho, o requests assume ISO-8859-1; nesse caso é melhor
            # deixar o BeautifulSoup detectar a codificação pela tag <meta charset>.
            declared = "charset=" in resp.headers.get("Content-Type", "").lower()
            soup = BeautifulSoup(resp.content, "html.parser", from_encoding=resp.encoding if declared else None)
            return Page(resp.url, soup)

    def open_stream(self, url: str) -> requests.Response:
        """Abre a URL sem baixar o corpo ainda (para downloads grandes). Use com `with`."""
        resp = self._get(url, stream=True)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            resp.close()
            raise
        return resp

    def _get(self, url: str, stream: bool = False) -> requests.Response:
        delay = self.cfg.delay
        if self.cfg.respect_robots_txt:
            robots = self._robots_for(url)
            if not robots.can_fetch(self.cfg.user_agent, url):
                raise BlockedByRobotsError(f"bloqueado pelo robots.txt de {_origin(url)}")
            delay = max(delay, robots.crawl_delay(self.cfg.user_agent) or 0)
        self._wait(delay)
        log.debug("GET %s", url)
        return self.session.get(url, timeout=self.cfg.timeout, stream=stream)

    def _wait(self, delay: float) -> None:
        remaining = self._last_request + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()

    def _robots_for(self, url: str) -> RobotFileParser:
        origin = _origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin]

    def _load_robots(self, origin: str) -> RobotFileParser:
        # Obs.: o parser da biblioteca padrão não entende curingas (* e $) nas regras.
        robots_url = f"{origin}/robots.txt"
        robots = RobotFileParser(robots_url)
        self._wait(self.cfg.delay)
        try:
            resp = self.session.get(robots_url, timeout=self.cfg.timeout)
            status = resp.status_code
        except requests.RequestException as e:
            resp, status, problem = None, None, type(e).__name__
        else:
            problem = f"HTTP {status}"

        if resp is not None and status < 400:
            robots.parse(resp.content.decode("utf-8", errors="replace").splitlines())
        elif resp is not None and status < 500:
            robots.allow_all = True  # o site não tem robots.txt: tudo liberado (RFC 9309)
        else:
            # Servidor fora do ar ou com erro: a norma manda assumir que nada é permitido.
            log.warning(
                "Não foi possível ler %s (%s). Por precaução, nada desse endereço será acessado "
                "nesta execução; tente de novo mais tarde.", robots_url, problem,
            )
            robots.disallow_all = True
        return robots


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
