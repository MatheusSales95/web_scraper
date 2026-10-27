import pytest
from bs4 import BeautifulSoup

from scraper.config import DEFAULT_EXTENSIONS, PostConfig
from scraper.extract import (
    ContentNotFoundError,
    DownloadLink,
    extract_post_links,
    extract_title,
    filename_in_text,
    find_download_links,
    find_next_page,
)
from scraper.http_client import Page

POST_URL = "https://blog.exemplo.com/post/1/"


def page(html: str, url: str = POST_URL) -> Page:
    return Page(url, BeautifulSoup(html, "html.parser"))


def urls(links: list[DownloadLink]) -> list[str]:
    return [link.url for link in links]


def test_post_links_are_absolute_unique_and_navigable():
    listing = page(
        """
        <article><a href="/post/1/"><img alt=""></a><h2><a href="/post/1/">Primeiro</a></h2></article>
        <article><h2><a href="post-2.html#comentarios">Segundo</a></h2></article>
        <article><h2><a href="javascript:void(0)">Quebrado</a></h2></article>
        <article><h2><a href="mailto:autor@exemplo.com">E-mail</a></h2></article>
        """,
        url="https://blog.exemplo.com/",
    )
    posts = extract_post_links(listing, "article a")
    assert [p.url for p in posts] == ["https://blog.exemplo.com/post/1/", "https://blog.exemplo.com/post-2.html"]


def test_selector_can_point_to_the_element_around_the_link():
    listing = page('<h2 class="titulo"><a href="/post/9/"> Nono  post </a></h2>', url="https://blog.exemplo.com/")
    [post] = extract_post_links(listing, "h2.titulo")
    assert post.url == "https://blog.exemplo.com/post/9/"
    assert post.title == "Nono post"


# Listagem em que o link envolve o card inteiro (título, miniatura, data e contagem de anexos).
CARD_LISTING = """
<a class="fancy-link image-link user-header__avatar" href="/servico/user/150695296"><img src="/icons/x"></a>
<menu>
  <li class="pagination-button-disabled pagination-button-current"><b>1</b></li>
  <a href="/servico/user/150695296?o=50" class="pagination-button-after-current"><b>2</b></a>
  <a href="/servico/user/150695296?o=50" class="next"><b>&gt;</b></a>
  <a href="/servico/user/150695296?o=600"><b>&gt;&gt;</b></a>
</menu>
<div class="card-list__items">
  <article class="post-card post-card--preview" data-id="171800964" data-user="150695296">
    <a href="/servico/user/150695296/post/171800964" class="image-link">
      <header class="post-card__header">
            Hsin - (Wuthering Waves)
      </header>
      <div class="post-card__image-container"><img class="post-card__image" src="/thumbnail/a.png" loading="lazy"></div>
      <footer class="post-card__footer"><div><div>
        <time class="timestamp " datetime="2026-10-08 15:28:51">2026-10-08 15:28</time>
        <div>5 attachments</div>
      </div><img src="/static/small_icons/x.png" alt=""></div></footer>
    </a>
  </article>
  <article class="post-card post-card--preview" data-id="171432516" data-user="150695296">
    <a href="/servico/user/150695296/post/171432516" class="image-link">
      <header class="post-card__header">
            Vote for your  ✨
      </header>
      <footer class="post-card__footer"><div><div>
        <time class="timestamp " datetime="2026-10-04 21:04:17">2026-10-04 21:04</time>
        <div>No attachments</div>
      </div></div></footer>
    </a>
  </article>
</div>
"""
CARD_LISTING_URL = "https://site.exemplo.com/servico/user/150695296"


def test_card_listing_title_selector_and_next_page():
    listing = page(CARD_LISTING, url=CARD_LISTING_URL)
    posts = extract_post_links(listing, "article.post-card > a", ".post-card__header")
    assert [(p.url, p.title, p.title_from_selector) for p in posts] == [
        (f"{CARD_LISTING_URL}/post/171800964", "Hsin - (Wuthering Waves)", True),
        (f"{CARD_LISTING_URL}/post/171432516", "Vote for your ✨", True),
    ]
    assert find_next_page(listing, "a.next") == f"{CARD_LISTING_URL}?o=50"


def test_without_title_selector_the_title_is_the_whole_link_text():
    [first, _] = extract_post_links(page(CARD_LISTING, url=CARD_LISTING_URL), "article.post-card > a")
    assert first.title == "Hsin - (Wuthering Waves) 2026-10-08 15:28 5 attachments"
    assert first.title_from_selector is False


def test_title_selector_that_finds_nothing_falls_back_to_the_link_text():
    listing = page('<h2><a href="/post/1/">Primeiro</a></h2>', url="https://blog.exemplo.com/")
    [post] = extract_post_links(listing, "h2 a", ".nao-existe")
    assert (post.title, post.title_from_selector) == ("Primeiro", False)


def test_download_attribute_gives_the_file_name():
    post = page(
        """
        <a class="fileThumb" href="https://cdn.exemplo.com/data/0d/18/0d187d09.png?f=pagina%201.png"
           download="pagina 1.png"><img src="/thumbnail/0d187d09.png"></a>
        <a href="https://cdn.exemplo.com/data/aa/42/aa42febb.zip" download="Pacote completo.zip">Download Pacote completo.zip</a>
        """
    )
    cfg = PostConfig(download_all_links=True, extensions=(".zip", ".png"))
    assert find_download_links(post, cfg) == [
        # o .zip vem primeiro: além do atributo e da extensão, o texto dele também é um nome de arquivo
        DownloadLink("https://cdn.exemplo.com/data/aa/42/aa42febb.zip", filename="Pacote completo.zip"),
        DownloadLink("https://cdn.exemplo.com/data/0d/18/0d187d09.png?f=pagina%201.png", filename="pagina 1.png"),
    ]


def test_heuristic_ranks_links_and_ignores_ordinary_ones():
    post = page(
        """
        <a href="/sobre/">Sobre o autor</a>
        <a href="/get/77" class="btn-download">Pegar</a>
        <a href="/dl/88">Baixar versão antiga</a>
        <a href="/files/livro.epub">Livro</a>
        """
    )
    links = find_download_links(post, PostConfig(download_all_links=True))
    assert urls(links) == [
        "https://blog.exemplo.com/files/livro.epub",  # extensão conhecida (3 pontos)
        "https://blog.exemplo.com/get/77",  # "download" na classe (2), empata e mantém a ordem da página
        "https://blog.exemplo.com/dl/88",  # "baixar" no texto (2)
    ]


def test_returns_only_the_most_likely_link_by_default():
    post = page('<a href="/dl/1">Baixar</a> <a href="/files/a.zip" download>Arquivo</a>')
    assert urls(find_download_links(post, PostConfig())) == ["https://blog.exemplo.com/files/a.zip"]


def test_link_whose_text_is_the_file_name():
    post = page(
        """
        <p>Leia antes o <a href="/post/anterior/">post anterior</a>.</p>
        <p><a href="/get/123">Projeto Final v2.zip (15 MB)</a></p>
        """
    )
    assert find_download_links(post, PostConfig()) == [
        DownloadLink("https://blog.exemplo.com/get/123", filename="Projeto Final v2.zip")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Projeto-Final_v2.ZIP", "Projeto-Final_v2.ZIP"),
        ("  Projeto Final v2.zip (15 MB) ", "Projeto Final v2.zip"),
        ("backup-completo.tar.gz", "backup-completo.tar.gz"),
        ("https://cdn.exemplo.com/a/b/manual.pdf", "manual.pdf"),
        ("Como abrir arquivos .zip", ""),  # extensão solta não é nome de arquivo
        ("arquivo.zipado", ""),
        ("Post anterior", ""),
    ],
)
def test_filename_in_text(text, expected):
    assert filename_in_text(text, DEFAULT_EXTENSIONS) == expected


def test_keywords_ignore_case_and_accents():
    post = page('<a href="/x/1">MUSICA completa</a>')
    assert urls(find_download_links(post, PostConfig(keywords=("música",)))) == ["https://blog.exemplo.com/x/1"]


def test_content_selector_limits_the_search():
    post = page('<article><p>Sem arquivo.</p></article><aside><a href="/catalogo.pdf">Baixar catálogo</a></aside>')
    assert find_download_links(post, PostConfig(content_selector="article")) == []


def test_missing_content_area_is_an_error():
    with pytest.raises(ContentNotFoundError):
        find_download_links(page("<div>layout diferente</div>"), PostConfig(content_selector="article"))


def test_explicit_selector_replaces_the_heuristic():
    post = page('<a href="/files/a.zip">Download</a><div class="mirrors"><a href="/espelho/1">Mirror 1</a></div>')
    links = find_download_links(post, PostConfig(download_link_selector=".mirrors a"))
    assert urls(links) == ["https://blog.exemplo.com/espelho/1"]


def test_explicit_selector_also_reads_the_file_name():
    post = page('<div class="anexos"><a href="/anexo/9">Relatório 2026.pdf</a></div>')
    links = find_download_links(post, PostConfig(download_link_selector=".anexos a"))
    assert links == [DownloadLink("https://blog.exemplo.com/anexo/9", filename="Relatório 2026.pdf")]


def test_base_tag_is_respected():
    post = page('<head><base href="https://cdn.exemplo.com/arquivos/"></head><a href="a.zip">Download</a>')
    assert urls(find_download_links(post, PostConfig())) == ["https://cdn.exemplo.com/arquivos/a.zip"]


def test_title_prefers_og_title_then_h1():
    assert extract_title(page('<title>Site | Post</title><meta property="og:title" content="Post"><h1>X</h1>')) == "Post"
    assert extract_title(page("<title>Site | Post</title><h1> Título\n do post </h1>")) == "Título do post"
