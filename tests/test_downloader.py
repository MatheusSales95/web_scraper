import pytest
import requests

from scraper.downloader import choose_filename, filename_from_disposition, sanitize_filename, unique_path


def response(url: str, disposition: str = "") -> requests.Response:
    resp = requests.Response()
    resp.url = url
    if disposition:
        resp.headers["Content-Disposition"] = disposition
    return resp


@pytest.mark.parametrize(
    ("resp", "link_name", "expected"),
    [
        # o nome indicado pelo servidor tem prioridade
        (response("https://x.com/f/a1b2.zip", 'attachment; filename="oficial.zip"'), "Projeto.zip", "oficial.zip"),
        # depois, o nome escrito no link
        (response("https://x.com/f/a1b2.zip"), "Projeto Final.zip", "Projeto Final.zip"),
        # depois, o fim da URL
        (response("https://x.com/f/a1b2.zip"), "", "a1b2.zip"),
        # por último, o título do post com a extensão do tipo de conteúdo
        (response("https://x.com/download.php?id=1"), "", "Título do post.zip"),
    ],
)
def test_choose_filename_priority(resp, link_name, expected):
    assert choose_filename(resp, "application/zip", "Título do post", link_name) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('a<b>:c"d|e?f*g.txt', "a_b__c_d_e_f_g.txt"),
        ("../../windows/system.ini", "_.._windows_system.ini"),
        ("CON.txt", "_CON.txt"),
        ("  nome com   espaço no fim . ", "nome com espaço no fim"),
        ("..", ""),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension():
    name = sanitize_filename("a" * 300 + ".zip")
    assert len(name) == 120
    assert name.endswith("a.zip")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('attachment; filename="manual final.pdf"', "manual final.pdf"),
        ("attachment; filename*=UTF-8''relat%C3%B3rio.csv", "relatório.csv"),
        ('attachment; filename="relatÃ³rio.csv"', "relatório.csv"),  # UTF-8 que chegou lido como Latin-1
        ("inline", ""),
        ("", ""),
    ],
)
def test_filename_from_disposition(header, expected):
    assert filename_from_disposition(header) == expected


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"1")
    assert unique_path(tmp_path / "a.zip") == tmp_path / "a (1).zip"
    assert unique_path(tmp_path / "b.zip") == tmp_path / "b.zip"
