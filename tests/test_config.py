import pytest

from scraper.config import ConfigError, load_config

MINIMAL = '[site]\nstart_url = "https://exemplo.com/"\npost_link_selector = "h2 a"\n'


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_and_paths_relative_to_the_config_file(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + '\n[post]\nextensions = ["ZIP", ".Rar"]\n'))
    assert cfg.files.download_dir == tmp_path.resolve() / "downloads"
    assert cfg.files.output_dir == tmp_path.resolve() / "output"
    assert cfg.post.extensions == (".zip", ".rar")
    assert cfg.http.delay == 1.0
    assert cfg.http.respect_robots_txt is True


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("\n[http]\ndelai = 2\n", "chave\\(s\\) desconhecida\\(s\\): delai"),
        ('\n[http]\ndelay = "2"\n', "http.delay deveria ser um número"),
        ('\n[post]\ncontent_selector = "div[[["\n', "seletor CSS inválido"),
        ('post_title_selector = "h2 >"\n', "site.post_title_selector: seletor CSS inválido"),  # continua em [site]
        ("\n[http]\ntimeout = 0\n", "timeout precisa ser maior que zero"),
        ("\n[extra]\na = 1\n", "seção\\(ões\\) desconhecida\\(s\\): extra"),
    ],
)
def test_friendly_errors(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, MINIMAL + extra))


def test_required_keys(tmp_path):
    with pytest.raises(ConfigError, match="start_url"):
        load_config(write(tmp_path, '[site]\npost_link_selector = "a"\n'))
