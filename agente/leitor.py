"""Leitura de arquivos enviados (CSV, TXT, XLSX, XLS, JSON) em DataFrames."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd

EXTENSOES_SUPORTADAS = {".csv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"}
CODIFICACOES = ("utf-8-sig", "utf-8", "latin-1", "cp1252")


def _decodificar(conteudo: bytes) -> str:
    for codificacao in CODIFICACOES:
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    texto = _decodificar(conteudo)
    amostra = texto[:20000]
    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        separador = ";" if amostra.count(";") > amostra.count(",") else ","
    # Tudo como texto: CPF/CNPJ/CEP perdem zeros à esquerda se lidos como número.
    return pd.read_csv(io.StringIO(texto), sep=separador, dtype=str, keep_default_na=False)


def _ler_json(conteudo: bytes) -> pd.DataFrame:
    dados = json.loads(_decodificar(conteudo))
    if isinstance(dados, dict):
        # Formato comum de APIs governamentais: {"dados": [...]} ou {"registros": [...]}
        listas = [v for v in dados.values() if isinstance(v, list)]
        dados = listas[0] if listas else [dados]
    return pd.json_normalize(dados).astype(str).replace({"nan": "", "None": ""})


def ler_arquivo(nome: str, conteudo: bytes) -> dict[str, pd.DataFrame]:
    """Lê um arquivo e devolve {nome_da_tabela: DataFrame}.

    Planilhas Excel com várias abas viram várias tabelas.
    """
    extensao = Path(nome).suffix.lower()
    base = Path(nome).stem
    if extensao not in EXTENSOES_SUPORTADAS:
        raise ValueError(
            f"Formato '{extensao}' não suportado. Use: {', '.join(sorted(EXTENSOES_SUPORTADAS))}"
        )

    if extensao in {".csv", ".txt"}:
        return {base: _ler_csv(conteudo)}
    if extensao == ".json":
        return {base: _ler_json(conteudo)}

    abas = pd.read_excel(io.BytesIO(conteudo), sheet_name=None, dtype=str)
    tabelas = {}
    for aba, df in abas.items():
        df = df.dropna(how="all").dropna(axis=1, how="all").fillna("")
        if df.empty:
            continue
        chave = base if len(abas) == 1 else f"{base}__{aba}"
        tabelas[chave] = df
    return tabelas


def ler_caminho(caminho: str | Path) -> dict[str, pd.DataFrame]:
    caminho = Path(caminho)
    return ler_arquivo(caminho.name, caminho.read_bytes())
