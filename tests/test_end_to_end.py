"""Execução completa do scraper contra o blog falso (tests/fake_site.py)."""

import csv
from pathlib import Path

import pytest

import fake_site
from scraper import downloader, runner
from scraper.__main__ import main
from scraper.runner import Runner

CONFIG = """
[site]
start_url = "{url}"
post_link_selector = "h2.entry-title a"
next_page_selector = "a.next"

[post]
content_selector = "article"

[files]
download_dir = "downloads"
output_dir = "output"

[http]
delay = 0
max_retries = 1
"""

EXPECTED_FILES = ["episodio-01.mp3", "exemplos-do-curso-v3.zip", "manual.pdf", "relatório de vendas.csv"]


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(downloader, "RETRY_PAUSE", 0)


@pytest.fixture
def config_file(site, tmp_path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.format(url=site.url), encoding="utf-8")
    return path


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def downloaded_files(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "downloads").iterdir())


def test_downloads_files_and_lists_failures(site, config_file, tmp_path):
    assert main(["-c", str(config_file)]) == 0

    # Só os arquivos dos posts: nada do catálogo da barra lateral e nenhum ".part" esquecido.
    assert downloaded_files(tmp_path) == EXPECTED_FILES
    downloads = tmp_path / "downloads"
    assert (downloads / "manual.pdf").read_bytes() == fake_site.PDF
    assert (downloads / "exemplos-do-curso-v3.zip").read_bytes() == fake_site.ZIP
    assert (downloads / "relatório de vendas.csv").read_bytes() == fake_site.CSV
    assert (downloads / "episodio-01.mp3").read_bytes() == fake_site.MP3

    failures = {row["url_post"].removeprefix(site.url): row for row in read_csv(tmp_path / "output" / "falhas.csv")}
    assert {post: row["motivo"] for post, row in failures.items()} == {
        "post/3/": "link_nao_encontrado",
        "post/4/": "erro_http",
        "post/5/": "nao_e_arquivo",
        "post/7/": "erro_http",
        "privado/post-8/": "bloqueado_robots_txt",
        "post/10/": "download_incompleto",
    }
    assert failures["post/3/"]["titulo"] == "Post sem link de download"
    assert failures["post/4/"]["url_download"] == f"{site.url}files/removido.zip"
    assert failures["post/4/"]["detalhes"] == "HTTP 404 Not Found"
    assert failures["post/7/"]["url_download"] == ""
    assert failures["post/7/"]["detalhes"] == "HTTP 500 Internal Server Error"

    assert len(read_csv(tmp_path / "output" / "baixados.csv")) == 4
    assert (tmp_path / "output" / "scraper.log").stat().st_size > 0


def test_second_run_skips_completed_posts_and_retries_failures(site, config_file, tmp_path):
    assert main(["-c", str(config_file)]) == 0
    first_run_requests = len(site.hits)

    assert main(["-c", str(config_file)]) == 0
    second_run = site.hits[first_run_requests:]
    assert "/post/1/" not in second_run
    assert "/files/manual.pdf" not in second_run
    assert "/post/3/" in second_run
    assert downloaded_files(tmp_path) == EXPECTED_FILES  # nada duplicado
    assert len(read_csv(tmp_path / "output" / "falhas.csv")) == 6


def test_ctrl_c_saves_progress_and_next_run_resumes(site, config_file, tmp_path, monkeypatch):
    process_post = Runner._process_post
    started = []

    def interrupt_on_third_post(self, post):
        started.append(post.url)
        if len(started) == 3:
            raise KeyboardInterrupt
        process_post(self, post)

    monkeypatch.setattr(Runner, "_process_post", interrupt_on_third_post)
    assert main(["-c", str(config_file)]) == 130
    assert downloaded_files(tmp_path) == ["exemplos-do-curso-v3.zip", "manual.pdf"]
    assert (tmp_path / "output" / "falhas.csv").exists()

    monkeypatch.setattr(Runner, "_process_post", process_post)
    requests_before = len(site.hits)
    assert main(["-c", str(config_file)]) == 0
    assert "/post/1/" not in site.hits[requests_before:]
    assert downloaded_files(tmp_path) == EXPECTED_FILES


def test_ctrl_c_during_download_leaves_no_partial_file(site, config_file, tmp_path, monkeypatch):
    class InterruptingProgressBar:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def update(self, n):
            raise KeyboardInterrupt

    monkeypatch.setattr(downloader, "_progress_bar", lambda name, total: InterruptingProgressBar())
    assert main(["-c", str(config_file)]) == 130
    assert downloaded_files(tmp_path) == []


def test_dry_run_does_not_download_or_record(site, config_file, tmp_path):
    assert main(["-c", str(config_file), "--dry-run"]) == 0
    assert not (tmp_path / "downloads").exists()
    assert not (tmp_path / "output" / "falhas.csv").exists()
    assert "/files/manual.pdf" not in site.hits


def test_max_posts(site, config_file, tmp_path):
    assert main(["-c", str(config_file), "--max-posts", "2"]) == 0
    assert downloaded_files(tmp_path) == ["exemplos-do-curso-v3.zip", "manual.pdf"]
    assert "/post/3/" not in site.hits


@pytest.mark.parametrize(
    ("title_selector", "expected"),
    [('post_title_selector = "h2"', "Manual em PDF"), ("", "Título da página do post")],
)
def test_post_title_selector_keeps_the_listing_title(site, config_file, tmp_path, monkeypatch, title_selector, expected):
    monkeypatch.setattr(runner, "extract_title", lambda page: "Título da página do post")
    text = config_file.read_text(encoding="utf-8").replace("h2.entry-title a", "article.resumo")
    config_file.write_text(text.replace("next_page_selector", f"{title_selector}\nnext_page_selector"), encoding="utf-8")
    assert main(["-c", str(config_file), "--max-posts", "1"]) == 0
    [row] = read_csv(tmp_path / "output" / "baixados.csv")
    assert row["titulo"] == expected


def test_broken_listing_page_is_reported(site, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.format(url=f"{site.url}nao-existe/"), encoding="utf-8")
    assert main(["-c", str(config)]) == 0
    [row] = read_csv(tmp_path / "output" / "falhas.csv")
    assert row["motivo"] == "erro_na_listagem"
    assert row["detalhes"] == "erro_http: HTTP 404 Not Found"


def test_invalid_config_returns_error_code(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text('[site]\nstart_url = "exemplo.com"\npost_link_selector = "a"\n', encoding="utf-8")
    assert main(["-c", str(config)]) == 2
    assert "start_url" in capsys.readouterr().err
