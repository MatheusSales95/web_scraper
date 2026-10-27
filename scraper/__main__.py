"""Linha de comando: python -m scraper [-c config.toml] [--dry-run] [--max-posts N] [-v]"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from tqdm import tqdm

from .config import Config, ConfigError, load_config
from .http_client import HttpClient
from .runner import Runner, Stats, human_size
from .storage import Store

log = logging.getLogger("scraper")


def main(argv: list[str] | None = None) -> int:
    _tolerate_unencodable_output()
    args = _parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Erro no arquivo de configuração: {e}", file=sys.stderr)
        return 2

    output_dir = cfg.files.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    with (
        _logging_setup(output_dir / "scraper.log", args.verbose),
        HttpClient(cfg.http) as client,
        closing(Store(output_dir / "estado.db")) as store,
    ):
        if args.dry_run:
            log.info("Modo simulação: os links serão procurados, mas nada será baixado nem registrado.")
        runner = Runner(cfg, client, store, dry_run=args.dry_run, max_posts=args.max_posts)
        try:
            runner.run()
        except KeyboardInterrupt:
            exit_code = 130
            log.warning("Interrompido pelo usuário. O progresso foi salvo; rode de novo para continuar de onde parou.")
        except Exception:
            exit_code = 1
            log.exception("Erro inesperado; a execução parou aqui (o progresso até este ponto foi salvo).")
        _finish(cfg, store, runner.stats, dry_run=args.dry_run)
    return exit_code


def _finish(cfg: Config, store: Store, stats: Stats, *, dry_run: bool) -> None:
    """Gera os relatórios CSV e mostra o resumo da execução."""
    log.info("=" * 60)
    log.info("Posts encontrados na listagem: %d", stats.posts_found)
    log.info("  pulados (já concluídos antes): %d", stats.skipped)
    log.info("  processados agora: %d (sucesso: %d, com falha: %d)", stats.processed, stats.ok, stats.failed)
    if dry_run:
        return
    output_dir = cfg.files.output_dir
    failures_csv, failures = store.export_failures(output_dir / "falhas.csv")
    downloads_csv, _ = store.export_downloads(output_dir / "baixados.csv")
    log.info("Arquivos baixados agora: %d (%s) em %s", stats.files, human_size(stats.bytes), cfg.files.download_dir)
    log.info("Itens para verificar manualmente: %d -> %s", failures, failures_csv)
    log.info("Todos os downloads já feitos: %s", downloads_csv)
    log.info("Log detalhado: %s", output_dir / "scraper.log")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scraper",
        description="Entra em cada post do site, baixa o arquivo do link de download e lista o que falhou.",
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=Path("config.toml"),
        help="arquivo de configuração (padrão: config.toml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="simulação: só procura os links de download, sem baixar nem registrar nada",
    )
    parser.add_argument(
        "--max-posts", type=_non_negative_int, default=0, metavar="N",
        help="processa no máximo N posts (útil para testar a configuração)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra também as mensagens de depuração")
    return parser.parse_args(argv)


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"esperado um número inteiro, veio {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("não pode ser negativo")
    return value


class _ConsoleHandler(logging.StreamHandler):
    """Escreve no terminal via tqdm.write, para as mensagens não atropelarem a barra de progresso."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=self.stream)
        except Exception:
            self.handleError(record)


class _ConsoleFormatter(logging.Formatter):
    PREFIXES = {logging.WARNING: "AVISO: ", logging.ERROR: "ERRO: ", logging.CRITICAL: "ERRO: "}

    def format(self, record: logging.LogRecord) -> str:
        return self.PREFIXES.get(record.levelno, "") + super().format(record)


@contextmanager
def _logging_setup(log_file: Path, verbose: bool) -> Iterator[None]:
    """Mensagens resumidas no terminal e completas (com data/hora) no arquivo de log."""
    console = _ConsoleHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(_ConsoleFormatter("%(message)s"))
    file = logging.FileHandler(log_file, encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file)
    try:
        yield
    finally:
        for handler in (console, file):
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


def _tolerate_unencodable_output() -> None:
    """Evita erro ao exibir títulos com caracteres que o terminal não suporta (ex.: emoji no Windows)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


if __name__ == "__main__":
    sys.exit(main())
