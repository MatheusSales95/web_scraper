# POCScraper

Percorre os posts de um site **um de cada vez**: abre o post, procura o link de download, baixa o
arquivo, volta para a lista de posts e segue para o próximo. Tudo o que não der certo (link não
encontrado, erro 404, página intermediária no lugar do arquivo...) vai para `output/falhas.csv`,
com o motivo, para você verificar manualmente depois.

## Como funciona

1. Abre a página principal (`start_url`) e coleta os links dos posts (`post_link_selector`).
2. Entra em cada post e procura o link de download: usa o seletor configurado ou, se não houver,
   a detecção automática (veja abaixo).
3. Baixa o arquivo para `downloads/` e segue para o próximo post da lista.
4. Terminada a página, segue o link de "próxima página" (`next_page_selector`), se houver.
5. Cada problema vira uma linha em `output/falhas.csv`.

A página principal é lida uma vez só: a lista de posts dela fica guardada na memória, então "voltar
para a página principal" não custa uma nova requisição ao site.

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se o PowerShell bloquear o `Activate.ps1`, rode uma vez:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Testar com o site de demonstração

O projeto traz um blog falso em que cada post simula uma situação diferente (download normal, post
sem link, arquivo removido, servidor fora do ar, bloqueio pelo robots.txt...):

```powershell
# terminal 1
python tests/fake_site.py

# terminal 2
python -m scraper -c config.demo.toml
```

Os resultados ficam em `demo/downloads` e `demo/output`.

## Usar em um site real

1. Edite o `config.toml`. Os campos principais são `start_url`, `post_link_selector`,
   `next_page_selector` e `content_selector`. Para descobrir os seletores, abra o site no
   navegador, clique com o botão direito no elemento e escolha **Inspecionar**.
   Se o link do post envolve o card inteiro (título, miniatura, data...), aponte o
   `post_title_selector` para o elemento que tem só o título.
2. Confira se a configuração encontra os posts e os links certos, sem baixar nada:
   ```powershell
   python -m scraper --dry-run --max-posts 3
   ```
3. Rode de verdade:
   ```powershell
   python -m scraper
   ```

| Opção | O que faz |
|---|---|
| `-c ARQUIVO` | Usa outro arquivo de configuração (padrão: `config.toml`). |
| `--dry-run` | Simulação: procura os links, mas não baixa nem registra nada. |
| `--max-posts N` | Processa no máximo N posts. |
| `-v` | Mostra também as mensagens de depuração no terminal. |

## Arquivos gerados

| Caminho | Conteúdo |
|---|---|
| `downloads/` | Os arquivos baixados. |
| `output/falhas.csv` | **A lista para verificação manual**: data, título, URL do post, URL do download, motivo e detalhes. |
| `output/baixados.csv` | Tudo o que já foi baixado e de qual post veio. |
| `output/scraper.log` | Log detalhado (todas as requisições, com data e hora). |
| `output/estado.db` | Estado interno (SQLite) usado para continuar de onde parou. |

Os CSVs usam `;` como separador e UTF-8 com BOM, então abrem direto no Excel em português. Se o
`falhas.csv` estiver aberto no Excel durante uma execução, o relatório novo é salvo com outro nome
(ex.: `falhas_20261010_143000.csv`).

## Motivos de falha

| Motivo | O que aconteceu |
|---|---|
| `link_nao_encontrado` | O post abriu, mas nenhum link parecia ser de download. |
| `conteudo_nao_encontrado` | O `content_selector` não achou a área do post (o layout pode ser diferente). |
| `nao_e_arquivo` | O link abriu uma página HTML, não um arquivo: encurtador, contagem regressiva, login, hospedagem externa etc. |
| `erro_http` | O servidor respondeu com erro (ex.: 404, 403, 500). Se `url_download` estiver vazio, o erro foi ao abrir o post. |
| `download_incompleto` | A conexão caiu no meio do download, mesmo depois das novas tentativas. |
| `timeout`, `erro_conexao`, `erro_requisicao` | Problemas de rede. |
| `bloqueado_robots_txt` | O robots.txt do site proíbe acessar essa URL. |
| `erro_ao_salvar` | Não foi possível gravar o arquivo (disco cheio, permissão etc.). |
| `erro_na_listagem` | Uma página da listagem não abriu; os posts dela e das páginas seguintes ficaram de fora. |
| `erro_inesperado` | Algo fora do previsto; o traceback completo fica no `scraper.log`. |

## Interromper e continuar

- Pode parar com **Ctrl+C** a qualquer momento. O progresso fica salvo e nenhum arquivo pela
  metade fica na pasta (o download é gravado como `.part` e só ganha o nome final quando termina).
- Na próxima execução, os posts concluídos são pulados e os que falharam são tentados de novo.
  O `falhas.csv` sempre mostra as falhas que ainda estão pendentes.
- Para recomeçar do zero, apague a pasta `output/`.

## Detecção automática do link de download

Quando `download_link_selector` está vazio, cada link da área de conteúdo recebe uma pontuação:

| Critério | Pontos |
|---|---|
| Tem o atributo HTML `download` | 3 |
| A URL termina com uma das `extensions` (`.zip`, `.pdf`...) | 3 |
| O texto do link é um nome de arquivo com uma das `extensions` (ex.: `Projeto Final v2.zip`) | 3 |
| Uma das `keywords` aparece no texto, `title`, `aria-label` ou `alt` da imagem do link | 2 |
| Uma das `keywords` aparece na classe ou no `id` do link | 2 |
| Uma das `keywords` aparece na URL | 1 |

Links com 2 pontos ou mais contam como download. Por padrão só o de maior pontuação é baixado;
com `download_all_links = true`, todos são.

Se o link não mostra a extensão nem no texto nem na URL (ex.: um link escrito "Projeto Final" que
aponta para `/arquivo/123`), a pontuação não tem como reconhecê-lo. Nesse caso, configure o
`download_link_selector` pela posição ou pelo endereço do link, e não pelo texto (que muda de post
para post). Exemplo: `.entry-content a[href*='/arquivo/']`. O seletor tem prioridade sobre a pontuação.

### Nome do arquivo salvo

1. O nome indicado pelo servidor (cabeçalho `Content-Disposition`), se houver.
2. Senão, o nome indicado pelo link: o valor do atributo `download`
   (ex.: `<a download="Projeto Final v2.zip">`) ou, se não houver, o nome escrito no texto do link.
3. Senão, o fim da URL (ex.: `.../uploads/manual.pdf` vira `manual.pdf`).
4. Senão, o título do post com a extensão do tipo de arquivo.

## Estrutura

```
scraper/
  __main__.py     linha de comando, log e resumo final
  config.py       leitura e validação do config.toml
  http_client.py  requisições: pausa entre elas, novas tentativas, robots.txt
  extract.py      links dos posts, próxima página, título e link de download
  downloader.py   download em streaming, nome de arquivo seguro, arquivo .part
  storage.py      estado em SQLite e geração dos CSVs
  runner.py       o fluxo: listagem -> post -> download -> próximo post
tests/
  fake_site.py    blog falso usado nos testes e na demonstração
```

## Testes

```powershell
python -m pytest
```

## Limitações

- **Sites que montam o conteúdo com JavaScript**: o scraper lê o HTML que o servidor envia, como o
  `curl` faria. Se os posts ou os links só aparecem depois que o JavaScript roda, nenhum post será
  encontrado (o terminal mostra um aviso). Nesse caso, a busca precisa ser feita por um navegador
  automatizado (Playwright ou Selenium).
- Downloads que exigem clicar em botões, esperar contagem regressiva, resolver captcha ou fazer
  login, e hospedagens como Mega e Google Drive, aparecem como `nao_e_arquivo` para você baixar manualmente.
- O leitor de robots.txt da biblioteca padrão do Python não entende curingas (`*`, `$`) nas regras.
- Mantenha um `delay` razoável e respeite os termos de uso do site que você estiver acessando.
