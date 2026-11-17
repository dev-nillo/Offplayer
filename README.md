# Offplayer

Plataforma local que importa uma playlist do Spotify (ou do YouTube), baixa o áudio uma vez e
toca direto do disco — sem anúncio, sem stream, sem depender de internet na hora de tocar.

**Formato:** link da playlist → catálogo resolvido → download em job → arquivo local com tag →
player estilo Spotify (sidebar, capa, fila, shuffle, repeat, busca, volume) servindo por HTTP
local com suporte a `Range`, então o scrub do player funciona de verdade.

---

## 1. Instalação

```powershell
cd "C:\Users\danxxz\Documents\Projeto Padrão"
python -m pip install -r Offplayer\requirements.txt
```

ou o bootstrap, que instala tudo e confere o motor:

```powershell
cd Offplayer
python bootstrap.py --ffmpeg
```

O bootstrap instala `yt-dlp`, `mutagen` e `imageio-ffmpeg` (esse último traz um binário de
ffmpeg pronto, então a conversão para mp3 funciona mesmo sem ffmpeg no PATH).

## 2. Rodando

```powershell
cd Offplayer
python run.py                 # abre http://127.0.0.1:8722
python run.py --port 9000
python run.py --host 0.0.0.0  # acessa de celular/TV na mesma rede
```

Primeiro uso: cole o link da playlist no topo → **Importar & baixar**.

## 3. Como a playlist é lida

O yt-dlp **não** tem extrator de Spotify — ele resolve a parte do YouTube. Então a leitura do
catálogo é feita em três camadas (`core/meta.py`), nesta ordem:

| Camada | Quando | Traz | Limite |
|---|---|---|---|
| **Spotify Web API (suas credenciais)** | `client_id`/`client_secret` preenchidos em Configurações | lista inteira com paginação (100/página), álbum, artistas estruturados, ISRC | nenhum |
| **Embed público do Spotify** | padrão, sem credencial | título, artistas, duração, uri e capa | ~50 faixas |
| **yt-dlp** | URL do YouTube / YouTube Music | lista completa, sem limite | — |

Credenciais são opcionais: um app grátis em developers.spotify.com → *Settings → Settings →
client_id/client_secret* → cole em Configurações. Sem elas o app ainda funciona, mas avisa
quando a playlist bateu no teto de ~50 faixas do embed.

O áudio vem do YouTube. Para cada faixa o app busca candidatos (`ytsearch8`), pontua por
similaridade de título, presença do artista, duração (tolerância de ±) e canal oficial
`- Topic`, e baixa o melhor. Match errado se corrige com o botão `↻` da linha.

## 4. O que acontece por baixo

| Etapa | Onde | Detalhe |
|---|---|---|
| resolve a playlist | `core/meta.py` | API oficial → embed público → yt-dlp, conforme a §3 |
| guarda no banco | `core/db.py` | SQLite (`playlists`, `tracks`, `jobs`) em WAL, para o servidor HTTP e o worker escreverem juntos |
| escolhe a fonte | `core/downloader.py` | `ytsearch8:` + scoring (`Downloader._score`) |
| baixa | `core/downloader.py` | `bestaudio` → extração via ffmpeg (mp3/m4a/opus) → progresso gravado no banco a cada 0,6 s |
| organiza | `core/library.py` | `Biblioteca/Artista/Álbum/Artista - Faixa.mp3` + tag ID3/MP4 (com `.json` ao lado como reserva) |
| toca | `server.py` + `web/` | `/api/track/<id>/file` responde `206` com `Content-Range` |

## 5. Endpoints

```
GET    /api/state                     estado completo (playlists, faixas, jobs, config, deps)
POST   /api/import                    {url, auto_download} -> importa e enfileira
GET    /api/playlist/<id>             playlist + faixas
POST   /api/playlist/<id>/download    {redownload?} -> job
POST   /api/playlist/<id>/cancel      {job_id} -> cancela o job
POST   /api/track/<id>/download       {youtube_id?} -> job de uma faixa (retry)
GET    /api/track/<id>/file           áudio local com suporte a Range (206)
GET    /api/track/<id>/cover          capa da faixa
GET    /api/cover/<playlist_id>       capa da playlist
DELETE /api/track/<id>                remove faixa (e o arquivo)
DELETE /api/playlist/<id>             remove playlist da biblioteca
POST   /api/settings                  {settings:{...}} -> grava config.json
POST   /api/shutdown                  encerra o servidor
```

## 6. Ajustes (`config.json`)

```json
{
  "library_dir": "C:\\Users\\danxxz\\Music\\Offplayer",
  "audio_format": "mp3",
  "audio_quality": "192",
  "prefer_topic": true,
  "host": "127.0.0.1",
  "port": 8722,
  "auto_open_browser": true,
  "progress_throttle": 0.6,
  "search_candidates": 8,
  "spotify_client_id": "",
  "spotify_client_secret": ""
}
```

`audio_format`: `mp3` | `m4a` | `opus` | `source` (mantém o container original).
`prefer_topic`: dá peso extra ao canal automático oficial "Artista - Topic" na escolha da fonte.

## 7. Atalhos no player

| Tecla | Ação |
|---|---|
| `Espaço` | play/pause |
| `←` / `→` | -5 s / +5 s |
| `Shift+←` / `Shift+→` | faixa anterior / próxima |

## 8. Quando algo falha

- **"yt-dlp não está instalado"** → `python -m pip install -r requirements.txt`
- **"O embed público do Spotify não devolveu dados"** → playlist privada, regional ou >50 faixas: preencha as credenciais da API em Configurações
- **"nenhum resultado para ..."** → a faixa não está no YouTube com esse nome; o `↻` tenta de novo e o `POST /api/track/<id>/download` com `youtube_id` fixa a fonte
- **conversão sem ffmpeg** → o app cai para o container original (`m4a`/`webm`), que o browser toca igual; instale ffmpeg com `python bootstrap.py --ffmpeg`
- **match errado** → o `↻` re-resolve; os pesos do scoring estão em `Downloader._score`

## 9. Ferramentas de desenvolvimento

```powershell
python smoke_test.py     # API, streaming com Range, UI, import real (sem baixar)
python test_pipeline.py  # resolve + baixa 2 faixas de verdade e confere as tags
python seed_demo.py      # importa uma playlist e popula a biblioteca com 3 faixas
```

## 10. Estrutura

```
offline_playlist/
├── run.py              entry point
├── bootstrap.py        instala dependências e checa o motor
├── config.py           config.json, pastas, probe de dependências
├── server.py           HTTP (stdlib) + API + streaming com Range
├── core/
│   ├── db.py           SQLite
│   ├── meta.py         resolução de playlist/track (API + embed + yt-dlp)
│   ├── downloader.py   fila de jobs, scoring de fonte, download
│   └── library.py      layout de pastas e tags
└── web/                index.html · style.css · app.js
```

## 11. Como rodar em dispositivos móveis

O app é um servidor Python + player web. O celular não roda Python nativamente, mas há duas
formas de usar, ambas testadas na prática:

**A) Celular consumindo o PC (mesma rede Wi-Fi)**
```powershell
# no PC
python run.py --host 0.0.0.0 --port 8722
```
Descubra o IP do PC (`ipconfig` → IPv4) e abra no celular `http://<ip>:8722`.
A UI é responsiva (sidebar vira menu, player compacto) e o app é um PWA: no Chrome/Edge do
celular use "Adicionar à tela inicial" → abre como app standalone, com ícone próprio e cache
do app shell. As faixas já baixadas tocam com a internet do celular desligada.

**B) Rodar no próprio celular Android (Termux) — download e playback 100% no aparelho**
```bash
# 1. instale o Termux pela F-Droid (NAO pela Play Store — a versao da Play Store esta desatualizada)
# 2. no Termux:
pkg install git
git clone https://github.com/dev-nillo/Offplayer.git && cd Offplayer
bash setup_termux.sh        # instala python, ffmpeg, dependencias e pede acesso ao storage
bash run_termux.sh          # sobe o servidor em 127.0.0.1:8722
```
Abra `http://127.0.0.1:8722` no navegador do próprio aparelho. A biblioteca cai em
`~/storage/music/Offplayer` (visível no app de Música/Arquivos do Android). iOS não tem
Termux — use a opção A.

Limitações honestas do Termux: instalação de ~300 MB, download consome bateria, e o Termux
precisa ficar aberto em segundo plano durante o download. Não é um app nativo, mas funciona.

**C) Acesso remoto (fora de casa)**
Túnel com `cloudflared tunnel --url http://127.0.0.1:8722` ou ngrok. O servidor ainda roda
no PC; o celular só consome. Atenção: sem autenticação, só exponha em rede confiável.

## 12. Como distribuir (exe / Docker)

**GitHub Pages não roda isso** — é hosting estático e o app tem backend Python (download,
SQLite, streaming com Range). Para distribuir, use uma das duas opções abaixo.

**A) Um único .exe (Windows)**
```powershell
cd Offplayer
python build_exe.py --clean
# gera dist/Offplayer.exe (~150-250 MB, inclui yt-dlp + ffmpeg embutido)
```
O .exe cria `data/` e `config.json` ao lado de si na primeira execução e abre o navegador
sozinho. Distribua o .exe; quem receber só clica.

**B) Container Docker**
```bash
cd Offplayer
docker build -t offplayer .
docker run -d --name offplayer -p 8722:8722 \
  -v offplayer-data:/app/data \
  -v offplayer-music:/app/Music \
  offplayer
```
Acesse `http://localhost:8722`. A biblioteca persiste nos volumes.

## 13. Verificado em campo

- `spotify:playlist` público lido pelo embed: 50 faixas, título e capa corretos
- faixas baixadas do YouTube em mp3 192 kbps (~9 MB cada), com tag ID3 (`TIT2`, `TPE1`, `TALB`, `TDRC`)
- duração do arquivo local batendo com a do Spotify (225,8 s → 225,9 s)
- player tocando do disco via `Range`/`206`, com scrub e tempo correndo
- shuffle: fila embaralhada com botão dedicado na playlist + toggle no player
- qualidade: formatos mp3/m4a/opus/source/best e qualidade 128/192/320/best (VBR máximo), com modo `best` sem re-encode
- PWA instalável com manifest + service worker + ícone, layout responsivo para mobile
