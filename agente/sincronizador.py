"""Cruzamento (sincronização) de várias tabelas por uma chave comum."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import pandas as pd

from . import validadores as v
from .normalizador import ResultadoTabela

# Tipos que identificam uma entidade (pessoa, empresa, município), em ordem de preferência.
TIPOS_CHAVE = ("cpf", "cnpj", "cpf_cnpj", "codigo_ibge")


@dataclass
class ResultadoSincronizacao:
    chave: str | None
    consolidado: pd.DataFrame
    cobertura: pd.DataFrame
    divergencias: pd.DataFrame
    tabelas_usadas: list[str] = field(default_factory=list)
    observacoes: list[str] = field(default_factory=list)


def _chave_normalizada(serie: pd.Series) -> pd.Series:
    """Só dígitos para documentos/códigos; texto maiúsculo sem acento para o resto."""
    def normalizar(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        texto = str(x).strip()
        digitos = v.somente_digitos(texto)
        if digitos and len(digitos) >= len(texto.replace(" ", "")) * 0.6:
            return digitos.lstrip("0") or "0"
        return v.remover_acentos(texto).upper() or None
    return serie.map(normalizar)


def _candidatas(tabelas: list[ResultadoTabela]) -> list[tuple[str, dict[str, str]]]:
    """Lista (nome_da_chave, {tabela: coluna}) possíveis, presentes em ≥2 tabelas."""
    candidatas = []
    for tipo in TIPOS_CHAVE:
        mapa = {}
        for t in tabelas:
            colunas = [c for c, tp in t.tipos.items() if tp == tipo]
            if colunas:
                mapa[t.nome] = colunas[0]
        if len(mapa) >= 2:
            candidatas.append((tipo, mapa))

    # CPF numa tabela e CPF/CNPJ em outra também casam.
    mapa_doc = {}
    for t in tabelas:
        for tipo in ("cpf_cnpj", "cpf", "cnpj"):
            colunas = [c for c, tp in t.tipos.items() if tp == tipo]
            if colunas:
                mapa_doc[t.nome] = colunas[0]
                break
    if len(mapa_doc) >= 2 and not any(set(m) == set(mapa_doc) for _, m in candidatas):
        candidatas.append(("documento", mapa_doc))

    # Colunas de mesmo nome (ex.: "matricula", "codigo_orgao") com valores em comum.
    contagem: dict[str, dict[str, str]] = {}
    for t in tabelas:
        for coluna, tipo in t.tipos.items():
            if tipo in {"texto", "numero"}:
                contagem.setdefault(coluna, {})[t.nome] = coluna
    for coluna, mapa in contagem.items():
        if len(mapa) >= 2:
            candidatas.append((coluna, mapa))
    return candidatas


def _pontuar(tabelas_por_nome: dict[str, ResultadoTabela], mapa: dict[str, str]) -> float:
    """Média de sobreposição de valores entre pares de tabelas e unicidade da chave."""
    conjuntos = {}
    for nome, coluna in mapa.items():
        valores = _chave_normalizada(tabelas_por_nome[nome].dados[coluna]).dropna()
        if valores.empty:
            return 0.0
        conjuntos[nome] = set(valores)
    pares = list(combinations(conjuntos.values(), 2))
    sobreposicao = sum(len(a & b) / min(len(a), len(b)) for a, b in pares) / len(pares)
    return sobreposicao * len(mapa)


def escolher_chave(tabelas: list[ResultadoTabela], chave_forcada: str | None = None):
    por_nome = {t.nome: t for t in tabelas}
    melhor, melhor_pontos = None, 0.0
    for nome_chave, mapa in _candidatas(tabelas):
        if chave_forcada and nome_chave != chave_forcada:
            continue
        pontos = _pontuar(por_nome, mapa)
        if nome_chave in TIPOS_CHAVE or nome_chave == "documento":
            pontos *= 1.5  # Identificadores oficiais são preferidos.
        if pontos > melhor_pontos:
            melhor, melhor_pontos = (nome_chave, mapa), pontos
    if melhor and melhor_pontos >= 0.1:
        return melhor
    return None


def _agregar(df: pd.DataFrame, chave: str, tipos: dict[str, str]) -> pd.DataFrame:
    """Uma linha por chave: soma valores, mantém o primeiro texto preenchido, conta registros."""
    regras = {}
    for coluna in df.columns:
        if coluna == chave:
            continue
        if tipos.get(coluna) == "valor":
            regras[coluna] = "sum"
        else:
            regras[coluna] = "first"
    agrupado = df.groupby(chave, dropna=True).agg(regras)
    agrupado["qtd_registros"] = df.groupby(chave, dropna=True).size()
    return agrupado


def sincronizar(tabelas: list[ResultadoTabela], chave_forcada: str | None = None) -> ResultadoSincronizacao:
    vazio = pd.DataFrame()
    if len(tabelas) < 2:
        return ResultadoSincronizacao(None, vazio, vazio, vazio, observacoes=[
            "Envie pelo menos 2 tabelas para cruzar os dados."
        ])

    escolha = escolher_chave(tabelas, chave_forcada)
    if escolha is None:
        return ResultadoSincronizacao(None, vazio, vazio, vazio, observacoes=[
            "Nenhuma chave comum (CPF, CNPJ, código IBGE ou coluna com mesmo nome) "
            "foi encontrada entre as tabelas."
        ])
    nome_chave, mapa = escolha
    por_nome = {t.nome: t for t in tabelas}
    observacoes = []

    partes = []
    exibicao: dict[str, pd.Series] = {}
    for nome_tabela, coluna in mapa.items():
        t = por_nome[nome_tabela]
        df = t.dados.copy()
        df["_chave"] = _chave_normalizada(df[coluna])
        sem_chave = int(df["_chave"].isna().sum())
        if sem_chave:
            observacoes.append(f"{nome_tabela}: {sem_chave} linha(s) sem '{coluna}' ficaram fora do cruzamento.")
        df = df.dropna(subset=["_chave"])
        for k, original in zip(df["_chave"], df[coluna]):
            exibicao.setdefault(k, original)
        df = df.drop(columns=[coluna])
        if df["_chave"].duplicated().any():
            observacoes.append(
                f"{nome_tabela}: chave repetida em várias linhas — valores somados e "
                f"demais campos com o primeiro preenchido (veja 'qtd_registros')."
            )
        agregado = _agregar(df, "_chave", t.tipos)
        agregado.columns = [f"{c}__{nome_tabela}" for c in agregado.columns]
        agregado[f"presente__{nome_tabela}"] = True
        partes.append(agregado)

    consolidado = partes[0]
    for parte in partes[1:]:
        consolidado = consolidado.join(parte, how="outer")

    presenca = [c for c in consolidado.columns if c.startswith("presente__")]
    consolidado[presenca] = consolidado[presenca].astype("boolean").fillna(False).astype(bool)
    consolidado.insert(0, "qtd_fontes", consolidado[presenca].sum(axis=1))
    consolidado.insert(
        1, "fontes",
        consolidado[presenca].apply(lambda r: ", ".join(c.split("__", 1)[1] for c, x in r.items() if x), axis=1),
    )
    consolidado.insert(0, nome_chave, [exibicao.get(k, k) for k in consolidado.index])
    consolidado = consolidado.reset_index(drop=True).sort_values(["qtd_fontes", nome_chave], ascending=[False, True])

    total = len(consolidado)
    cobertura = pd.DataFrame([
        {
            "tabela": nome,
            "coluna_chave": mapa[nome],
            "chaves_distintas": int(consolidado[f"presente__{nome}"].sum()),
            "pct_do_total": round(100 * consolidado[f"presente__{nome}"].sum() / total, 1) if total else 0,
            "exclusivas_desta_tabela": int(
                (consolidado[f"presente__{nome}"] & (consolidado["qtd_fontes"] == 1)).sum()
            ),
        }
        for nome in mapa
    ])
    em_todas = int((consolidado["qtd_fontes"] == len(mapa)).sum())
    observacoes.insert(0, f"Chave usada: '{nome_chave}'. {em_todas} de {total} registros aparecem em todas as {len(mapa)} tabelas.")

    divergencias = _divergencias(consolidado, nome_chave, list(mapa))
    if not divergencias.empty:
        observacoes.append(f"{len(divergencias)} divergência(s) entre fontes para o mesmo registro.")

    return ResultadoSincronizacao(
        chave=nome_chave,
        consolidado=consolidado.reset_index(drop=True),
        cobertura=cobertura,
        divergencias=divergencias,
        tabelas_usadas=list(mapa),
        observacoes=observacoes,
    )


def _comparavel(x):
    if x is None or (not isinstance(x, str) and pd.isna(x)):
        return None
    if isinstance(x, float):
        return round(x, 2)
    if isinstance(x, pd.Timestamp):
        return x.date()
    return v.remover_acentos(str(x)).strip().upper()


def _divergencias(consolidado: pd.DataFrame, nome_chave: str, tabelas: list[str]) -> pd.DataFrame:
    """Mesmo campo (ex.: 'nome') com valores diferentes em fontes diferentes."""
    campos: dict[str, list[str]] = {}
    for coluna in consolidado.columns:
        if "__" in coluna and not coluna.startswith(("presente__", "qtd_registros__")):
            campo, tabela = coluna.split("__", 1)
            if tabela in tabelas:
                campos.setdefault(campo, []).append(coluna)

    linhas = []
    for campo, colunas in campos.items():
        if len(colunas) < 2:
            continue
        for _, registro in consolidado.iterrows():
            valores = {c.split("__", 1)[1]: registro[c] for c in colunas}
            comparaveis = {t: _comparavel(x) for t, x in valores.items()}
            distintos = {x for x in comparaveis.values() if x is not None}
            if len(distintos) > 1:
                linha = {nome_chave: registro[nome_chave], "campo": campo}
                linha.update({f"valor__{t}": valores[t] for t in valores})
                linhas.append(linha)
    return pd.DataFrame(linhas)
