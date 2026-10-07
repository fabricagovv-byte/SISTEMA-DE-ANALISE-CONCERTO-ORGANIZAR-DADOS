"""Leitura de arquivos de dados (CSV, TXT, XLSX, XLS, JSON) em DataFrames.

Cada DataFrame devolvido carrega em `df.attrs["leitura"]` o que foi encontrado e corrigido
na leitura (codificação, separador, linha do cabeçalho, linhas de lixo removidas...).
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pandas as pd

from .validadores import corrigir_mojibake

EXTENSOES_SUPORTADAS = {".csv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"}
CODIFICACOES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
LINHA_TOTAL = re.compile(r"^\s*(total|totais|subtotal|soma|somat[óo]rio|tot\.)\b", re.IGNORECASE)


def decodificar(conteudo: bytes) -> tuple[str, str]:
    for codificacao in CODIFICACOES:
        try:
            return conteudo.decode(codificacao), ("latin-1/cp1252" if codificacao == "cp1252" else codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace"), "latin-1"


def _limpar(df: pd.DataFrame, info: dict) -> pd.DataFrame:
    """Corrige acentos quebrados, apara cabeçalhos e remove linhas vazias e de TOTAL."""
    originais = [str(c) for c in df.columns]
    df.columns = [re.sub(r"\s+", " ", corrigir_mojibake(c)).strip() for c in originais]
    sujos = [o for o, n in zip(originais, df.columns) if o != n]
    if sujos:
        info["cabecalhos_corrigidos"] = sujos
    df = df.map(lambda x: corrigir_mojibake(x) if isinstance(x, str) else x)

    texto = df.map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x).strip())
    texto = texto.replace({"nan": "", "None": ""})
    vazias = texto.eq("").all(axis=1)
    primeira = texto.apply(lambda linha: next((x for x in linha if x), ""), axis=1)
    totais = primeira.map(lambda x: bool(LINHA_TOTAL.match(str(x))))
    if vazias.sum():
        info["linhas_vazias_removidas"] = int(vazias.sum())
    if totais.sum():
        info["linhas_total_removidas"] = int(totais.sum())
    df = df[~vazias & ~totais].reset_index(drop=True)
    return df.dropna(axis=1, how="all")


def _achar_cabecalho(bruto: pd.DataFrame) -> int:
    """Primeira linha (entre as 15 iniciais) que parece cabeçalho: vários textos curtos preenchidos."""
    preenchidas = bruto.notna() & bruto.astype(str).apply(lambda c: c.str.strip().ne(""))
    maximo = int(preenchidas.sum(axis=1).max() or 0)
    for i in range(min(15, len(bruto))):
        linha = bruto.iloc[i][preenchidas.iloc[i]]
        if len(linha) >= max(2, 0.6 * maximo) and all(
            isinstance(x, str) and len(x) <= 60 and not re.fullmatch(r"[\d.,\-/ ]+", x) for x in linha
        ):
            return i
    return 0


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    texto, codificacao = decodificar(conteudo)
    amostra = texto[:20000]
    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        separador = ";" if amostra.count(";") > amostra.count(",") else ","
    # Tudo como texto: CPF/CNPJ/CEP perdem zeros à esquerda se lidos como número.
    df = pd.read_csv(io.StringIO(texto), sep=separador, dtype=str, keep_default_na=False)
    info = {"formato": "CSV", "codificacao": codificacao, "separador": separador}
    df = _limpar(df, info)
    df.attrs["leitura"] = info
    return df


def _ler_json(conteudo: bytes) -> pd.DataFrame:
    texto, codificacao = decodificar(conteudo)
    dados = json.loads(texto)
    if isinstance(dados, dict):
        # Formato comum de APIs governamentais: {"dados": [...]} ou {"registros": [...]}
        listas = [v for v in dados.values() if isinstance(v, list)]
        dados = listas[0] if listas else [dados]
    esquemas = {tuple(sorted(d)) for d in dados if isinstance(d, dict)}
    tipos_id: dict[str, set[str]] = {}
    for d in dados:
        if isinstance(d, dict):
            for k, x in d.items():
                if re.search(r"id$|^id|_id", k, re.IGNORECASE) and x is not None:
                    tipos_id.setdefault(k, set()).add("texto" if isinstance(x, str) else "número")
    # Listas (ex.: itens do pedido) viram texto JSON; objetos aninhados viram colunas "pai.filho".
    def textual(v):
        # Tudo vira texto já aqui: evita que IDs 2 virem 2.0 quando a coluna tem vazios.
        if isinstance(v, dict):
            return {k: textual(x) for k, x in v.items()}
        if isinstance(v, list):
            return json.dumps(v, ensure_ascii=False)
        return None if v is None else str(v)

    registros = [textual(d) for d in dados if isinstance(d, dict)]
    df = pd.json_normalize(registros)
    df = df.map(lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))
    info = {"formato": "JSON", "codificacao": codificacao, "esquemas_diferentes": len(esquemas)}
    if len({t for ts in tipos_id.values() for t in ts}) > 1:
        info["ids_tipos_misturados"] = {k: sorted(t) for k, t in tipos_id.items()}
    df = _limpar(df, info)
    df.attrs["leitura"] = info
    return df


def ler_arquivo(nome: str, conteudo: bytes) -> dict[str, pd.DataFrame]:
    """Lê um arquivo e devolve {nome_da_tabela: DataFrame}.

    Planilhas Excel com várias abas viram várias tabelas; abas de rascunho são descartadas
    (ficam registradas em `df.attrs["leitura"]["abas_descartadas"]` da primeira aba útil).
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

    abas = pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, dtype=object)
    tabelas, descartadas = {}, []
    for aba, bruto in abas.items():
        bruto = bruto.dropna(how="all").dropna(axis=1, how="all")
        if bruto.size == 0 or len(bruto) < 3 or int(bruto.notna().sum().sum()) < 6:
            descartadas.append(f"{aba} (vazia/rascunho: {int(bruto.notna().sum().sum())} célula(s))")
            continue
        linha = _achar_cabecalho(bruto)
        cabecalho = [str(c).strip() if pd.notna(c) else f"coluna_{i + 1}" for i, c in enumerate(bruto.iloc[linha])]
        df = bruto.iloc[linha + 1:].copy()
        df.columns = cabecalho
        df = df.map(lambda x: None if pd.isna(x) else (x if isinstance(x, str) else _celula_texto(x)))
        info = {"formato": "Excel", "aba": aba}
        if linha:
            info["cabecalho_na_linha"] = int(bruto.index[linha]) + 1
            acima = [str(x) for x in bruto.iloc[:linha].to_numpy().ravel() if pd.notna(x)]
            info["titulo_ignorado"] = " | ".join(acima)[:120]
        df = _limpar(df, info)
        chave = base if len(abas) == 1 else f"{base}__{aba}"
        df.attrs["leitura"] = info
        tabelas[chave] = df
    if tabelas and descartadas:
        next(iter(tabelas.values())).attrs["leitura"]["abas_descartadas"] = descartadas
    if not tabelas:
        raise ValueError("Planilha sem dados (todas as abas vazias ou de rascunho).")
    return tabelas


def _celula_texto(x) -> str:
    if hasattr(x, "isoformat"):
        return x.isoformat(sep=" ") if hasattr(x, "hour") else x.isoformat()
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def ler_caminho(caminho: str | Path) -> dict[str, pd.DataFrame]:
    caminho = Path(caminho)
    return ler_arquivo(caminho.name, caminho.read_bytes())
