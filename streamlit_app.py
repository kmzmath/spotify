"""
Math Spotify Dashboard - versão web.

Este app foi adaptado para rodar online com Streamlit.
O usuário envia os arquivos pelo navegador:
- YourLibrary.json, opcional, para identificar músicas curtidas.
- Arquivos Streaming_History_Audio_*.json, múltiplos.
- Opcionalmente, um ZIP contendo os JSONs exportados pelo Spotify.

Rodar localmente:
    python -m pip install -r requirements.txt
    python -m streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Iterable

import pandas as pd
import streamlit as st


# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

DATA_INICIO_PADRAO = date(2016, 10, 22)
DATA_FINAL_PADRAO = date.today()
TEMPO_MINIMO_MS = 35_000
MAX_UPLOAD_MB = 1024


@dataclass(frozen=True)
class JsonUpload:
    nome: str
    conteudo: object
    origem: str


# =============================================================================
# LEITURA E NORMALIZAÇÃO
# =============================================================================


def _decode_json_bytes(raw: bytes, nome: str) -> object:
    """Lê JSON enviado por upload. Aceita UTF-8 com ou sem BOM."""
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")

    try:
        return json.loads(texto)
    except json.JSONDecodeError as erro:
        raise ValueError(f"{nome}: JSON inválido ({erro})") from erro


def _pegar_primeiro(registro: dict, chaves: Iterable[str], padrao=None):
    for chave in chaves:
        valor = registro.get(chave)
        if valor not in (None, ""):
            return valor
    return padrao


def _parse_data(ts: str) -> date | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        try:
            data = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(data):
                return None
            return data.date()
        except Exception:
            return None


def ler_json_upload(uploaded_file, origem: str) -> JsonUpload:
    raw = uploaded_file.getvalue()
    return JsonUpload(
        nome=uploaded_file.name,
        conteudo=_decode_json_bytes(raw, uploaded_file.name),
        origem=origem,
    )


def ler_jsons_zip(uploaded_zip) -> list[JsonUpload]:
    """Extrai todos os .json de um ZIP enviado pelo usuário."""
    if uploaded_zip is None:
        return []

    jsons: list[JsonUpload] = []
    try:
        with zipfile.ZipFile(BytesIO(uploaded_zip.getvalue())) as arquivo_zip:
            for info in arquivo_zip.infolist():
                if info.is_dir():
                    continue
                if not info.filename.lower().endswith(".json"):
                    continue

                with arquivo_zip.open(info) as arquivo:
                    raw = arquivo.read()

                jsons.append(
                    JsonUpload(
                        nome=info.filename,
                        conteudo=_decode_json_bytes(raw, info.filename),
                        origem=f"ZIP: {uploaded_zip.name}",
                    )
                )
    except zipfile.BadZipFile as erro:
        raise ValueError("O arquivo ZIP enviado está inválido ou corrompido.") from erro

    return jsons


def separar_uploads(
    library_file,
    history_files: list,
    zip_file,
) -> tuple[JsonUpload | None, list[JsonUpload], list[str]]:
    avisos: list[str] = []

    library_upload: JsonUpload | None = None
    history_uploads: list[JsonUpload] = []

    # 1) Arquivo de biblioteca enviado separadamente tem prioridade.
    if library_file is not None:
        library_upload = ler_json_upload(library_file, "Upload direto")

    # 2) Históricos enviados separadamente.
    for arquivo in history_files or []:
        try:
            item = ler_json_upload(arquivo, "Upload direto")
        except ValueError as erro:
            avisos.append(str(erro))
            continue

        if isinstance(item.conteudo, list):
            history_uploads.append(item)
        elif isinstance(item.conteudo, dict) and item.nome.lower().endswith("yourlibrary.json"):
            if library_upload is None:
                library_upload = item
        else:
            avisos.append(f"{item.nome}: ignorado porque não parece ser histórico de streaming.")

    # 3) ZIP exportado do Spotify.
    if zip_file is not None:
        for item in ler_jsons_zip(zip_file):
            nome_base = item.nome.split("/")[-1].lower()

            if isinstance(item.conteudo, dict) and nome_base == "yourlibrary.json":
                if library_upload is None:
                    library_upload = item
                continue

            if isinstance(item.conteudo, list):
                # Spotify Extended Streaming History vem como lista de registros.
                # Aceitamos qualquer JSON em lista; registros sem música/artista serão filtrados depois.
                history_uploads.append(item)
                continue

            avisos.append(f"{item.nome}: ignorado porque não é lista de histórico nem YourLibrary.json.")

    return library_upload, history_uploads, avisos


@st.cache_data(show_spinner=False)
def carregar_curtidas_do_json(nome: str, conteudo: object) -> set[str]:
    if not isinstance(conteudo, dict):
        raise ValueError(f"{nome}: YourLibrary.json deveria conter um objeto JSON.")

    curtidas: set[str] = set()
    for item in conteudo.get("tracks", []):
        artista = item.get("artist")
        musica = item.get("track")
        if artista and musica:
            curtidas.add(f"{artista} | {musica}")

    return curtidas


@st.cache_data(show_spinner=False)
def carregar_historico_dos_jsons(upload_key: tuple[tuple[str, str], ...]) -> pd.DataFrame:
    """
    Recebe uma tupla serializável de (nome, json_string).
    Usamos string JSON para permitir cache estável no Streamlit.
    """
    linhas: list[dict] = []

    for nome, json_string in upload_key:
        try:
            registros = json.loads(json_string)
        except Exception as erro:
            st.warning(f"Não foi possível ler {nome}: {erro}")
            continue

        if not isinstance(registros, list):
            st.warning(f"{nome}: ignorado porque não contém uma lista de registros.")
            continue

        for registro in registros:
            if not isinstance(registro, dict):
                continue

            data_stream = _parse_data(registro.get("ts"))
            if data_stream is None:
                continue

            musica = _pegar_primeiro(
                registro,
                ["master_metadata_track_name", "masterMetadataTrackName", "trackName"],
            )
            artista = _pegar_primeiro(
                registro,
                [
                    "master_metadata_album_artist_name",
                    "masterMetadataAlbumArtistName",
                    "artistName",
                ],
            )
            album = _pegar_primeiro(
                registro,
                ["master_metadata_album_album_name", "masterMetadataAlbumAlbumName", "albumName"],
                "Sem álbum",
            )

            # Mantém só áudio musical. Podcasts/audiobooks ficam fora da lógica original.
            if not musica or not artista:
                continue

            ms_played = _pegar_primeiro(registro, ["ms_played", "msPlayed"], 0) or 0
            try:
                ms_played = int(ms_played)
            except (TypeError, ValueError):
                ms_played = 0

            skipped = bool(registro.get("skipped", False))
            faixa = f"{artista} | {musica}"
            album_chave = f"{artista} | {album}"

            linhas.append(
                {
                    "data": data_stream,
                    "timestamp": registro.get("ts"),
                    "plataforma": registro.get("platform", ""),
                    "pais": registro.get("conn_country", ""),
                    "artista": artista,
                    "musica": musica,
                    "album": album,
                    "faixa": faixa,
                    "album_chave": album_chave,
                    "uri": registro.get("spotify_track_uri"),
                    "ms_played": ms_played,
                    "minutos": ms_played / 60_000,
                    "skipped_spotify": skipped,
                    "skip_curto": bool(skipped and ms_played < TEMPO_MINIMO_MS),
                    "arquivo_origem": nome,
                }
            )

    colunas = [
        "data",
        "timestamp",
        "plataforma",
        "pais",
        "artista",
        "musica",
        "album",
        "faixa",
        "album_chave",
        "uri",
        "ms_played",
        "minutos",
        "skipped_spotify",
        "skip_curto",
        "arquivo_origem",
    ]

    if not linhas:
        return pd.DataFrame(columns=colunas)

    df = pd.DataFrame(linhas, columns=colunas)
    df["data"] = pd.to_datetime(df["data"])
    return df.sort_values("data").reset_index(drop=True)


def uploads_para_cache(history_uploads: list[JsonUpload]) -> tuple[tuple[str, str], ...]:
    """Converte uploads já carregados em uma chave serializável para cache."""
    itens: list[tuple[str, str]] = []
    for item in history_uploads:
        if isinstance(item.conteudo, list):
            itens.append((item.nome, json.dumps(item.conteudo, ensure_ascii=False, separators=(",", ":"))))
    return tuple(itens)


# =============================================================================
# FILTROS E CÁLCULOS
# =============================================================================


def aplicar_filtros(
    df: pd.DataFrame,
    data_inicio: date,
    data_final: date,
    filtro_curtidas: str,
    musicas_curtidas: set[str],
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    inicio = pd.Timestamp(data_inicio)
    fim = pd.Timestamp(data_final)
    filtrado = df[(df["data"] >= inicio) & (df["data"] <= fim)].copy()

    if filtro_curtidas == "Curtidas":
        filtrado = filtrado[filtrado["faixa"].isin(musicas_curtidas)]
    elif filtro_curtidas == "Não curtidas":
        filtrado = filtrado[~filtrado["faixa"].isin(musicas_curtidas)]

    return filtrado


def nome_coluna_entidade(tipo: str) -> str:
    mapa = {
        "Músicas": "faixa",
        "Artistas": "artista",
        "Álbuns": "album_chave",
    }
    return mapa[tipo]


def gerar_ranking(df: pd.DataFrame, coluna: str, top_n: int, minimo_streams: int = 1) -> pd.DataFrame:
    if df.empty or coluna not in df.columns:
        return pd.DataFrame()

    ranking = (
        df.groupby(coluna, dropna=True)
        .agg(
            streams=(coluna, "size"),
            horas=("ms_played", lambda s: round(s.sum() / 3_600_000, 2)),
            skips=("skip_curto", "sum"),
            primeira_vez=("data", "min"),
            ultima_vez=("data", "max"),
        )
        .reset_index()
    )
    ranking = ranking[ranking["streams"] >= minimo_streams]
    if ranking.empty:
        return ranking

    ranking["skip_%"] = (ranking["skips"] / ranking["streams"] * 100).round(2)
    ranking["primeira_vez"] = ranking["primeira_vez"].dt.date
    ranking["ultima_vez"] = ranking["ultima_vez"].dt.date
    ranking = ranking.sort_values(["streams", "horas"], ascending=[False, False]).head(top_n)
    ranking.insert(0, "posição", range(1, len(ranking) + 1))
    return ranking


def gerar_cumulativo(df: pd.DataFrame, coluna: str, top_n: int, data_inicio: date, data_final: date) -> pd.DataFrame:
    if df.empty or coluna not in df.columns:
        return pd.DataFrame()

    top_itens = df.groupby(coluna).size().nlargest(top_n).index.tolist()
    if not top_itens:
        return pd.DataFrame()

    base = df[df[coluna].isin(top_itens)]
    diario = base.groupby(["data", coluna]).size().unstack(fill_value=0)
    indice = pd.date_range(pd.Timestamp(data_inicio), pd.Timestamp(data_final), freq="D")
    diario = diario.reindex(indice, fill_value=0)
    cumulativo = diario.cumsum()
    cumulativo.index.name = "data"
    return cumulativo


def gerar_mensal(df: pd.DataFrame, coluna: str, item: str) -> pd.DataFrame:
    if df.empty or not item:
        return pd.DataFrame()

    base = df[df[coluna] == item].copy()
    if base.empty:
        return pd.DataFrame()

    base["mês"] = base["data"].dt.to_period("M").dt.to_timestamp()
    mensal = base.groupby("mês").size().reset_index(name="streams")
    return mensal.set_index("mês")


def calcular_favoritas(df: pd.DataFrame, data_final: date, top_n: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    diario = df.groupby(["faixa", "data"]).size().reset_index(name="streams_dia")
    diario = diario.sort_values(["faixa", "data"])
    diario["acumulado"] = diario.groupby("faixa")["streams_dia"].cumsum()

    quinta = diario[diario["acumulado"] >= 5].groupby("faixa")["data"].min()
    total = df.groupby("faixa").size()

    resultado = pd.DataFrame({"primeira_5a_ouvida": quinta, "streams": total})
    resultado = resultado.dropna(subset=["primeira_5a_ouvida"])
    if resultado.empty:
        return resultado

    fim = pd.Timestamp(data_final)
    resultado["dias_desde_5a_ouvida"] = (fim - resultado["primeira_5a_ouvida"]).dt.days + 1
    resultado = resultado[resultado["dias_desde_5a_ouvida"] > 0]
    resultado["ouvidas_por_dia"] = (
        resultado["streams"] / resultado["dias_desde_5a_ouvida"]
    ).round(4)
    resultado["primeira_5a_ouvida"] = resultado["primeira_5a_ouvida"].dt.date
    resultado = resultado.sort_values("ouvidas_por_dia", ascending=False).head(top_n)
    resultado = resultado.reset_index().rename(columns={"faixa": "música"})
    resultado.insert(0, "posição", range(1, len(resultado) + 1))
    return resultado


def calcular_meta(df: pd.DataFrame, data_final: date, top_n: int) -> pd.DataFrame:
    favoritas = calcular_favoritas(df, data_final, top_n=100_000)
    if favoritas.empty:
        return favoritas

    resultado = favoritas.copy()
    resultado["primeira_5a_ouvida_ts"] = pd.to_datetime(resultado["primeira_5a_ouvida"])
    fim = pd.Timestamp(data_final)
    resultado["tempo_desde_5a_ouvida"] = (fim - resultado["primeira_5a_ouvida_ts"]).dt.days

    max_media = resultado["ouvidas_por_dia"].max()
    max_tempo = resultado["tempo_desde_5a_ouvida"].max()

    resultado["norm_ouvidas_por_dia"] = (
        resultado["ouvidas_por_dia"] / max_media if max_media else 0
    )
    resultado["norm_tempo"] = resultado["tempo_desde_5a_ouvida"] / max_tempo if max_tempo else 0
    resultado["meta_score"] = (
        resultado["norm_ouvidas_por_dia"] * resultado["norm_tempo"]
    ).round(5)

    resultado = resultado.drop(columns=["posição", "primeira_5a_ouvida_ts"])
    resultado = resultado.sort_values("meta_score", ascending=False).head(top_n).reset_index(drop=True)
    resultado.insert(0, "posição", range(1, len(resultado) + 1))
    return resultado


def calcular_odiadas(df: pd.DataFrame, top_n: int, minimo_streams: int) -> pd.DataFrame:
    ranking = gerar_ranking(df, "faixa", top_n=100_000, minimo_streams=minimo_streams)
    if ranking.empty:
        return ranking

    ranking = ranking.sort_values(["skip_%", "skips", "streams"], ascending=[False, False, False])
    ranking = ranking.head(top_n).reset_index(drop=True)
    ranking["posição"] = range(1, len(ranking) + 1)
    return ranking.rename(columns={"faixa": "música"})


def calcular_recomendacao_exclusao(
    df: pd.DataFrame,
    musicas_curtidas: set[str],
    data_final: date,
    top_n: int,
    minimo_streams: int,
    somente_curtidas: bool,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    base = df.copy()
    if somente_curtidas:
        base = base[base["faixa"].isin(musicas_curtidas)]

    if base.empty:
        return pd.DataFrame()

    favoritas = calcular_favoritas(base, data_final, top_n=100_000)
    odiadas = calcular_odiadas(base, top_n=100_000, minimo_streams=minimo_streams)
    if favoritas.empty or odiadas.empty:
        return pd.DataFrame()

    fav = favoritas[["música", "ouvidas_por_dia", "streams", "primeira_5a_ouvida"]].copy()
    odi = odiadas[["música", "skip_%", "skips"]].copy()
    resultado = fav.merge(odi, on="música", how="inner")
    resultado = resultado[(resultado["streams"] >= minimo_streams) & (resultado["skip_%"] > 0)]
    if resultado.empty:
        return resultado

    max_ouvidas = resultado["ouvidas_por_dia"].max() or 1
    max_skip = resultado["skip_%"].max() or 1
    resultado["ouvidas_por_dia_norm"] = resultado["ouvidas_por_dia"] / max_ouvidas
    resultado["skip_norm"] = resultado["skip_%"] / max_skip
    resultado = resultado[resultado["skip_norm"] > 0]
    resultado["score_exclusao"] = (
        resultado["ouvidas_por_dia_norm"] / resultado["skip_norm"]
    ).round(5)

    resultado = resultado.sort_values("score_exclusao", ascending=True).head(top_n)
    resultado = resultado.reset_index(drop=True)
    resultado.insert(0, "posição", range(1, len(resultado) + 1))
    return resultado


def csv_download(df: pd.DataFrame, nome: str, label: str):
    if df.empty:
        return
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(label, csv, file_name=nome, mime="text/csv")


def formatar_numero(valor: int | float, casas: int = 0) -> str:
    if casas == 0:
        return f"{valor:,.0f}".replace(",", ".")
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# =============================================================================
# INTERFACE
# =============================================================================


def render_upload_screen() -> tuple[JsonUpload | None, list[JsonUpload]]:
    st.info(
        "Envie o `YourLibrary.json` e os arquivos `Streaming_History_Audio_*.json`. "
        "Você também pode enviar um ZIP com esses JSONs dentro."
    )

    with st.expander("Onde encontro esses arquivos?", expanded=False):
        st.markdown(
            """
            No pacote de dados do Spotify, normalmente:
            - `Spotify Account Data/YourLibrary.json`
            - `Spotify Extended Streaming History/Streaming_History_Audio_*.json`

            O app não precisa que os nomes sejam exatamente esses para os históricos,
            desde que os JSONs sejam listas de registros de streaming.
            """
        )

    col1, col2 = st.columns(2)

    with col1:
        library_file = st.file_uploader(
            "YourLibrary.json — opcional, usado para filtro de curtidas",
            type=["json"],
            accept_multiple_files=False,
            max_upload_size=MAX_UPLOAD_MB,
            key="library_file",
        )

    with col2:
        zip_file = st.file_uploader(
            "ZIP completo — opcional",
            type=["zip"],
            accept_multiple_files=False,
            max_upload_size=MAX_UPLOAD_MB,
            key="zip_file",
        )

    history_files = st.file_uploader(
        "Histórico de streaming — envie vários JSONs",
        type=["json"],
        accept_multiple_files=True,
        max_upload_size=MAX_UPLOAD_MB,
        key="history_files",
    )

    try:
        library_upload, history_uploads, avisos = separar_uploads(
            library_file=library_file,
            history_files=history_files,
            zip_file=zip_file,
        )
    except ValueError as erro:
        st.error(str(erro))
        st.stop()

    for aviso in avisos:
        st.warning(aviso)

    if not history_uploads:
        st.stop()

    return library_upload, history_uploads


def main() -> None:
    st.set_page_config(
        page_title="Math Spotify Dashboard",
        page_icon="🎧",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Math Spotify Dashboard")
    st.caption("Dashboard online para analisar histórico estendido do Spotify enviado pelo usuário.")

    library_upload, history_uploads = render_upload_screen()

    with st.spinner("Carregando histórico..."):
        upload_key = uploads_para_cache(history_uploads)
        df = carregar_historico_dos_jsons(upload_key)

    if df.empty:
        st.error("Os arquivos foram recebidos, mas nenhum registro musical válido foi carregado.")
        st.stop()

    if library_upload is not None:
        try:
            musicas_curtidas = carregar_curtidas_do_json(library_upload.nome, library_upload.conteudo)
        except Exception as erro:
            st.warning(f"Não foi possível ler curtidas: {erro}")
            musicas_curtidas = set()
    else:
        musicas_curtidas = set()

    data_min = df["data"].min().date()
    data_max = df["data"].max().date()

    with st.sidebar:
        st.header("Filtros")
        periodo = st.date_input(
            "Período",
            value=(data_min, data_max),
            min_value=data_min,
            max_value=data_max,
            format="YYYY-MM-DD",
        )
        if isinstance(periodo, tuple) and len(periodo) == 2:
            data_inicio, data_final = periodo
        else:
            data_inicio, data_final = data_min, data_max

        top_n = st.slider("Top N", min_value=5, max_value=200, value=20, step=5)
        minimo_streams = st.slider("Mínimo de streams para rankings de skip", 1, 50, 5)

        opcoes_filtro = ["Todas"]
        if musicas_curtidas:
            opcoes_filtro.extend(["Curtidas", "Não curtidas"])
        filtro_curtidas = st.selectbox("Filtro de biblioteca", opcoes_filtro)

        st.divider()
        if st.button("Limpar cache/reprocessar", width="stretch"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption("Arquivos carregados nesta sessão")
        st.write(f"Históricos: {len(history_uploads)}")
        st.write(f"Curtidas: {len(musicas_curtidas)}")

    df_filtrado = aplicar_filtros(df, data_inicio, data_final, filtro_curtidas, musicas_curtidas)

    aba_resumo, aba_rankings, aba_graficos, aba_mensal, aba_especiais, aba_arquivos = st.tabs(
        ["Resumo", "Rankings", "Gráficos", "Mensal", "Especiais", "Arquivos"]
    )

    with aba_resumo:
        st.subheader("Resumo do período")
        if df_filtrado.empty:
            st.warning("Nenhum registro encontrado para os filtros selecionados.")
        else:
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Streams", formatar_numero(len(df_filtrado)))
            col2.metric("Horas ouvidas", formatar_numero(df_filtrado["ms_played"].sum() / 3_600_000, 1))
            col3.metric("Músicas únicas", formatar_numero(df_filtrado["faixa"].nunique()))
            col4.metric("Artistas únicos", formatar_numero(df_filtrado["artista"].nunique()))
            skip_pct = df_filtrado["skip_curto"].mean() * 100 if len(df_filtrado) else 0
            col5.metric("Skips curtos", f"{skip_pct:.2f}%".replace(".", ","))

            st.markdown("#### Distribuição diária")
            diario = df_filtrado.groupby("data").size().rename("streams")
            st.line_chart(diario)

            st.markdown("#### Top rápido")
            col_a, col_b = st.columns(2)
            with col_a:
                st.write("Músicas")
                top_musicas = gerar_ranking(df_filtrado, "faixa", 10)
                st.dataframe(top_musicas, width="stretch", hide_index=True)
            with col_b:
                st.write("Artistas")
                top_artistas = gerar_ranking(df_filtrado, "artista", 10)
                st.dataframe(top_artistas, width="stretch", hide_index=True)

    with aba_rankings:
        st.subheader("Rankings")
        tipo_ranking = st.radio("Tipo", ["Músicas", "Artistas", "Álbuns"], horizontal=True)
        coluna = nome_coluna_entidade(tipo_ranking)
        ranking = gerar_ranking(df_filtrado, coluna, top_n)
        if ranking.empty:
            st.warning("Sem dados para esse ranking.")
        else:
            nome_item = {"faixa": "música", "artista": "artista", "album_chave": "álbum"}[coluna]
            ranking_view = ranking.rename(columns={coluna: nome_item})
            st.dataframe(ranking_view, width="stretch", hide_index=True)
            csv_download(ranking_view, f"ranking_{tipo_ranking.lower()}.csv", "Baixar ranking CSV")

    with aba_graficos:
        st.subheader("Gráfico cumulativo")
        tipo_grafico = st.radio("Entidade", ["Músicas", "Artistas", "Álbuns"], horizontal=True, key="tipo_grafico")
        coluna_grafico = nome_coluna_entidade(tipo_grafico)
        cumulativo = gerar_cumulativo(df_filtrado, coluna_grafico, top_n, data_inicio, data_final)
        if cumulativo.empty:
            st.warning("Sem dados para plotar.")
        else:
            st.line_chart(cumulativo)
            cumulativo_csv = cumulativo.reset_index()
            csv_download(cumulativo_csv, f"cumulativo_{tipo_grafico.lower()}.csv", "Baixar cumulativo CSV")

    with aba_mensal:
        st.subheader("Ouvidas mensais")
        tipo_mensal = st.radio("Tipo", ["Músicas", "Artistas", "Álbuns"], horizontal=True, key="tipo_mensal")
        coluna_mensal = nome_coluna_entidade(tipo_mensal)

        ranking_base = gerar_ranking(df_filtrado, coluna_mensal, top_n=500)
        if ranking_base.empty:
            st.warning("Sem dados para seleção mensal.")
        else:
            opcoes = ranking_base[coluna_mensal].tolist()
            busca = st.text_input("Filtrar opções", "")
            if busca.strip():
                opcoes_filtradas = [op for op in opcoes if busca.lower() in str(op).lower()]
            else:
                opcoes_filtradas = opcoes

            if not opcoes_filtradas:
                st.warning("Nenhum item encontrado com esse filtro.")
            else:
                item = st.selectbox("Item", opcoes_filtradas)
                mensal = gerar_mensal(df_filtrado, coluna_mensal, item)
                if mensal.empty:
                    st.warning("Sem dados mensais para esse item.")
                else:
                    st.line_chart(mensal)
                    st.dataframe(mensal.reset_index(), width="stretch", hide_index=True)
                    csv_download(mensal.reset_index(), "ouvidas_mensais.csv", "Baixar mensal CSV")

    with aba_especiais:
        st.subheader("Análises especiais")
        opcao = st.selectbox(
            "Análise",
            ["Músicas favoritas", "META música", "Músicas odiadas", "Recomendação de exclusão"],
        )

        if opcao == "Músicas favoritas":
            resultado = calcular_favoritas(df_filtrado, data_final, top_n)
            st.caption("Ordena músicas pela média de ouvidas por dia após atingirem 5 ouvidas no período.")
        elif opcao == "META música":
            resultado = calcular_meta(df_filtrado, data_final, top_n)
            st.caption("Score = recorrência diária normalizada × tempo desde a 5ª ouvida normalizado.")
        elif opcao == "Músicas odiadas":
            resultado = calcular_odiadas(df_filtrado, top_n, minimo_streams)
            st.caption("Skip curto = skipped=True e menos de 35 segundos tocados.")
        else:
            somente_curtidas = st.checkbox(
                "Analisar somente músicas curtidas",
                value=True,
                disabled=not musicas_curtidas,
            )
            if not musicas_curtidas:
                st.info("Envie o YourLibrary.json para limitar a análise às músicas curtidas.")

            resultado = calcular_recomendacao_exclusao(
                df_filtrado,
                musicas_curtidas,
                data_final,
                top_n,
                minimo_streams,
                somente_curtidas and bool(musicas_curtidas),
            )
            st.caption("Menor score aparece primeiro: baixa recorrência + alta taxa de skip.")

        if resultado.empty:
            st.warning("Sem dados suficientes para essa análise.")
        else:
            st.dataframe(resultado, width="stretch", hide_index=True)
            csv_download(resultado, f"{opcao.lower().replace(' ', '_')}.csv", "Baixar resultado CSV")

    with aba_arquivos:
        st.subheader("Arquivos carregados")
        col1, col2, col3 = st.columns(3)
        col1.metric("Históricos carregados", len(history_uploads))
        col2.metric("Registros musicais", formatar_numero(len(df)))
        col3.metric("Músicas curtidas", formatar_numero(len(musicas_curtidas)))

        st.markdown("#### Históricos usados")
        historicos_df = pd.DataFrame(
            [
                {
                    "arquivo": item.nome,
                    "origem": item.origem,
                    "tipo": "histórico",
                }
                for item in history_uploads
            ]
        )
        st.dataframe(historicos_df, width="stretch", hide_index=True)

        st.markdown("#### Biblioteca")
        if library_upload is None:
            st.info("Nenhum YourLibrary.json carregado. Os filtros de curtidas ficam indisponíveis.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "arquivo": library_upload.nome,
                            "origem": library_upload.origem,
                            "músicas curtidas": len(musicas_curtidas),
                        }
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

        st.markdown("#### Diagnóstico dos dados carregados")
        diag = pd.DataFrame(
            [
                {"métrica": "Registros musicais carregados", "valor": formatar_numero(len(df))},
                {"métrica": "Primeira data no histórico", "valor": data_min.isoformat()},
                {"métrica": "Última data no histórico", "valor": data_max.isoformat()},
                {"métrica": "Registros no período filtrado", "valor": formatar_numero(len(df_filtrado))},
                {"métrica": "Campo de tempo usado", "valor": "ms_played com fallback para msPlayed"},
                {"métrica": "Regra de skip curto", "valor": "skipped=True e ms_played < 35000"},
                {"métrica": "Persistência no servidor", "valor": "Não grava arquivos em disco; processa uploads em memória/sessão."},
            ]
        )
        st.dataframe(diag, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
