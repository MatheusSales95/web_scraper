"""Blog falso para testar e demonstrar o scraper sem depender de um site real.

Demonstração:
    python tests/fake_site.py                  (deixe rodando: http://127.0.0.1:8000/)
    python -m scraper -c config.demo.toml      (em outro terminal)

Cada post simula uma situação: botão "Download", link cujo texto é o nome do arquivo, post sem
link, arquivo removido (404), link que abre uma página intermediária, post fora do ar (500), post
proibido pelo robots.txt e servidor que cai no meio do download. A barra lateral tem um link "Baixar o catálogo" que NÃO deve ser
baixado quando `content_selector` limita a busca ao <article>.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# Conteúdo dos arquivos (os testes comparam com o que foi salvo em disco).
PDF = b"%PDF-1.4\n% manual de teste\n" + b"0" * 4096
ZIP = b"PK\x05\x06" + b"\x00" * 18  # um .zip vazio, mas válido
CSV = "produto;quantidade\nmaçã;3\n".encode("utf-8")
MP3 = bytes(range(256)) * 512  # 128 KB
UNSTABLE = bytes(range(256)) * 256  # o servidor "cai" na metade deste

ROBOTS = "User-agent: *\nDisallow: /privado/\n"

# (caminho, título, conteúdo do post). Conteúdo None = o post responde com erro 500.
POSTS = [
    ("/post/1/", "Manual em PDF", '<p>Segue o manual.</p><p><a class="btn" href="/files/manual.pdf">Download</a></p>'),
    ("/post/2/", "Pacote de exemplos", '<p>Exemplos do curso:</p><p><a href="/anexo/2">exemplos-do-curso-v3.zip</a></p>'),
    ("/post/3/", "Post sem link de download", '<p>Só texto. Leia também o <a href="/post/1/">manual</a>.</p>'),
    ("/post/4/", "Arquivo removido do servidor", '<a href="/files/removido.zip">Download</a>'),
    ("/post/5/", "Download por página intermediária", '<a href="/aguarde/5/">Download</a>'),
    ("/post/6/", "Planilha com nome definido pelo servidor", '<a class="download-button" href="/arquivo?id=6">Clique aqui</a>'),
    ("/post/7/", "Post fora do ar", None),
    ("/privado/post-8/", "Post proibido pelo robots.txt", '<a href="/files/privado.zip">Download</a>'),
    ("/post/9/", "Podcast em MP3", '<a href="/files/episodio-01.mp3" download>Ouvir offline</a>'),
    ("/post/10/", "Servidor instável no meio do download", '<a href="/files/instavel.zip">Download</a>'),
]


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    truncate: bool = False  # envia só metade do corpo e fecha a conexão


def _html(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><title>{escape(title)} | Blog de Teste</title>
<meta property="og:title" content="{escape(title)}"></head>
<body>
<header><a href="/">Blog de Teste</a></header>
<main>{body}</main>
<aside class="sidebar"><h3>Destaque</h3><a href="/files/catalogo.pdf">Baixar o catálogo</a></aside>
</body></html>""".encode("utf-8")


def _listing(posts: list, next_page: str | None) -> bytes:
    items = "".join(
        f'<article class="resumo"><a href="{path}"><img src="/thumb.png" alt=""></a>'
        f'<h2 class="entry-title"><a href="{path}">{escape(title)}</a></h2></article>'
        for path, title, _ in posts
    )
    pagination = f'<nav><a class="next" href="{next_page}">Próxima página »</a></nav>' if next_page else ""
    return _html("Início", items + pagination)


def _build_routes() -> dict[str, Response]:
    routes = {
        "/robots.txt": Response(200, ROBOTS.encode(), "text/plain"),
        "/": Response(200, _listing(POSTS[:5], next_page="/page/2/")),
        "/page/2/": Response(200, _listing(POSTS[5:], next_page=None)),
        "/aguarde/5/": Response(200, _html("Aguarde", "<p>Seu download começará em 10 segundos...</p>")),
        "/files/manual.pdf": Response(200, PDF, "application/pdf"),
        "/anexo/2": Response(200, ZIP, "application/zip"),  # URL sem nome: o nome só aparece no texto do link
        "/files/privado.zip": Response(200, ZIP, "application/zip"),
        "/files/catalogo.pdf": Response(200, PDF, "application/pdf"),
        "/files/episodio-01.mp3": Response(200, MP3, "audio/mpeg"),
        "/files/instavel.zip": Response(200, UNSTABLE, "application/zip", truncate=True),
        "/arquivo?id=6": Response(
            200, CSV, "text/csv",
            {"Content-Disposition": "attachment; filename*=UTF-8''relat%C3%B3rio%20de%20vendas.csv"},
        ),
    }
    for path, title, content in POSTS:
        if content is None:
            routes[path] = Response(500, _html("Erro", "<h1>Erro interno do servidor</h1>"))
        else:
            routes[path] = Response(200, _html(title, f"<article><h1>{escape(title)}</h1>{content}</article>"))
    return routes


ROUTES = _build_routes()
NOT_FOUND = Response(404, _html("Não encontrado", "<h1>Página não encontrada</h1>"))


class _Handler(BaseHTTPRequestHandler):
    server: FakeSite

    def do_GET(self) -> None:
        self.server.hits.append(self.path)
        resp = ROUTES.get(self.path, NOT_FOUND)
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(resp.body)))
        for name, value in resp.headers.items():
            self.send_header(name, value)
        self.end_headers()
        # HTTP/1.0: a conexão fecha ao fim da resposta, então no truncado o cliente percebe que faltou metade.
        self.wfile.write(resp.body[: len(resp.body) // 2] if resp.truncate else resp.body)

    def log_message(self, format: str, *args) -> None:
        if self.server.verbose:
            super().log_message(format, *args)


class FakeSite(ThreadingHTTPServer):
    def __init__(self, port: int = 0, verbose: bool = False):
        super().__init__(("127.0.0.1", port), _Handler)
        self.hits: list[str] = []  # caminhos requisitados, em ordem (usado nos testes)
        self.verbose = verbose

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/"

    def start_in_background(self) -> FakeSite:
        Thread(target=self.serve_forever, daemon=True).start()
        return self

    def handle_error(self, request, client_address) -> None:
        if self.verbose:  # nos testes, conexões encerradas pelo cliente não interessam
            super().handle_error(request, client_address)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blog falso para testar o scraper.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    site = FakeSite(args.port, verbose=True)
    print(f"Blog falso rodando em {site.url}  (Ctrl+C para parar)")
    try:
        site.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        site.server_close()


if __name__ == "__main__":
    main()
