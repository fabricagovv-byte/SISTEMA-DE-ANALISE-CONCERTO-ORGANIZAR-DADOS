"""Detecção de tipos de coluna e limpeza/padronização de cada tabela."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from . import validadores as v

TIPOS = (
    "cpf", "cnpj", "cpf_cnpj", "data", "valor", "uf", "cep", "codigo_ibge", "numero", "texto",
)

# Pistas no nome da coluna (já sem acento, minúsculo, snake_case).
PISTAS_NOME = {
    "cpf": ("cpf",),
    "cnpj": ("cnpj",),
    "cpf_cnpj": ("cpf_cnpj", "cnpj_cpf", "documento", "nr_doc", "cpfcnpj", "inscricao"),
    "data": ("data", "dt_", "dt", "dia", "vencimento", "nascimento", "competencia"),
    "valor": (
        "valor", "vlr", "vl_", "preco", "montante", "saldo", "empenhado", "liquidado",
        "pago", "repasse", "salario", "remuneracao", "total", "custo",
    ),
    "uf": ("uf", "estado", "sg_uf", "sigla_uf"),
    "cep": ("cep",),
    "codigo_ibge": ("ibge", "cod_municipio", "codigo_municipio", "cd_municipio", "id_municipio"),
}


@dataclass
class ResultadoTabela:
    nome: str
    dados: pd.DataFrame
    tipos: dict[str, str]
    problemas: list[dict] = field(default_factory=list)
    linhas_originais: int = 0
    duplicadas_removidas: int = 0
    renomeadas: dict[str, str] = field(default_factory=dict)


def padronizar_nome_coluna(nome: str) -> str:
    texto = v.remover_acentos(str(nome)).strip().lower()
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto or "coluna"


def _tem_pista(coluna: str, tipo: str) -> bool:
    partes = coluna.split("_")
    for pista in PISTAS_NOME.get(tipo, ()):
        if pista.endswith("_"):
            if coluna.startswith(pista):
                return True
        elif pista in partes or (len(pista) > 3 and pista in coluna):
            return True
    return False


def _proporcao(serie: pd.Series, funcao) -> float:
    if serie.empty:
        return 0.0
    return float(serie.map(funcao).mean())


def detectar_tipo(coluna: str, serie: pd.Series) -> str:
    valores = serie.astype(str).str.strip()
    valores = valores[valores != ""]
    if valores.empty:
        return "texto"
    amostra = valores.sample(min(len(valores), 500), random_state=0)
    digitos = amostra.map(v.somente_digitos)
    tamanhos = digitos.str.len()

    if _tem_pista(coluna, "cpf_cnpj") or (_tem_pista(coluna, "cpf") and _tem_pista(coluna, "cnpj")):
        return "cpf_cnpj"
    if _tem_pista(coluna, "cnpj") or _proporcao(amostra, v.cnpj_valido) > 0.8 and (tamanhos >= 13).mean() > 0.8:
        return "cnpj"
    if _tem_pista(coluna, "cpf") or _proporcao(amostra, v.cpf_valido) > 0.8 and (tamanhos.between(10, 11)).mean() > 0.8:
        return "cpf"
    if _tem_pista(coluna, "cep"):
        return "cep"
    if _tem_pista(coluna, "codigo_ibge") and (tamanhos.isin([6, 7])).mean() > 0.8:
        return "codigo_ibge"
    eh_uf = _proporcao(amostra, lambda x: v.normalizar_uf(x) is not None)
    if eh_uf > 0.95 or (_tem_pista(coluna, "uf") and eh_uf > 0.7):
        return "uf"

    eh_data = _proporcao(amostra, lambda x: v.converter_data(x) is not None)
    if eh_data > 0.8 and (_tem_pista(coluna, "data") or not amostra.str.fullmatch(r"\d+").all()):
        return "data"

    eh_numero = _proporcao(amostra, lambda x: v.converter_valor_monetario(x) is not None)
    tem_simbolo_moeda = amostra.str.contains(r"R\$|,\d{2}$", regex=True).mean() > 0.5
    if (eh_numero > 0.9 and tem_simbolo_moeda) or (eh_numero > 0.6 and _tem_pista(coluna, "valor")):
        return "valor"
    if (tamanhos >= 12).mean() > 0.5:
        return "texto"  # cartões, protocolos, chaves: números longos não são quantidades
    if amostra.str.fullmatch(r"-?\d+([.,]\d+)?").mean() > 0.95:
        # Códigos com zero à esquerda (ex.: matrícula "00123") são texto, não número.
        if amostra.str.match(r"^0\d").any():
            return "texto"
        return "numero"
    return "texto"


def _registrar(problemas, tabela, linha, coluna, valor, descricao):
    problemas.append({
        "tabela": tabela,
        "linha": int(linha) + 2,  # +2: cabeçalho e base 1, como no Excel
        "coluna": coluna,
        "valor_original": valor,
        "problema": descricao,
    })


def _limpar_coluna(serie: pd.Series, tipo: str, tabela: str, coluna: str, problemas: list) -> pd.Series:
    limpa = []
    for indice, bruto in serie.items():
        valor = re.sub(r"\s+", " ", str(bruto)).strip()
        if valor.lower() in {"", "nan", "none", "null", "-", "n/a", "na", "nd"}:
            limpa.append(None)
            continue

        if tipo == "cpf":
            if v.cpf_valido(valor):
                limpa.append(v.formatar_cpf(valor))
            else:
                _registrar(problemas, tabela, indice, coluna, valor, "CPF inválido")
                limpa.append(valor)
        elif tipo == "cnpj":
            if v.cnpj_valido(valor):
                limpa.append(v.formatar_cnpj(valor))
            else:
                _registrar(problemas, tabela, indice, coluna, valor, "CNPJ inválido")
                limpa.append(valor)
        elif tipo == "cpf_cnpj":
            digitos = v.somente_digitos(valor)
            if len(digitos) <= 11 and v.cpf_valido(valor):
                limpa.append(v.formatar_cpf(valor))
            elif v.cnpj_valido(valor):
                limpa.append(v.formatar_cnpj(valor))
            else:
                _registrar(problemas, tabela, indice, coluna, valor, "CPF/CNPJ inválido")
                limpa.append(valor)
        elif tipo == "data":
            data = v.converter_data(valor)
            if data is None:
                _registrar(problemas, tabela, indice, coluna, valor, "Data não reconhecida")
            limpa.append(data)
        elif tipo in {"valor", "numero"}:
            numero = v.converter_valor_monetario(valor)
            if numero is None:
                _registrar(problemas, tabela, indice, coluna, valor, "Número/valor não reconhecido")
            limpa.append(numero)
        elif tipo == "uf":
            uf = v.normalizar_uf(valor)
            if uf is None:
                _registrar(problemas, tabela, indice, coluna, valor, "UF inválida")
            limpa.append(uf or valor.upper())
        elif tipo == "cep":
            cep = v.formatar_cep(valor)
            if cep is None:
                _registrar(problemas, tabela, indice, coluna, valor, "CEP inválido")
            limpa.append(cep or valor)
        elif tipo == "codigo_ibge":
            digitos = v.somente_digitos(valor)
            if len(digitos) not in (6, 7):
                _registrar(problemas, tabela, indice, coluna, valor, "Código IBGE com tamanho inválido")
            limpa.append(digitos or valor)
        else:
            limpa.append(valor)

    resultado = pd.Series(limpa, index=serie.index, dtype=object)
    if tipo == "data":
        resultado = pd.to_datetime(resultado, errors="coerce")
    elif tipo in {"valor", "numero"}:
        resultado = pd.to_numeric(resultado, errors="coerce")
        if tipo == "numero" and resultado.notna().any() and (resultado.dropna() % 1 == 0).all():
            resultado = resultado.astype("Int64")  # IDs e contagens: 1, não 1.0
    return resultado


def normalizar_tabela(
    nome: str,
    df: pd.DataFrame,
    renomear: dict[str, str] | None = None,
    tipos_forcados: dict[str, str] | None = None,
) -> ResultadoTabela:
    """Padroniza nomes de colunas, detecta tipos, limpa valores e remove duplicatas.

    `renomear` mapeia nome original -> nome padronizado (ex.: sugerido pela IA).
    `tipos_forcados` mapeia nome padronizado -> tipo, sobrepondo a detecção automática.
    """
    renomear = renomear or {}
    tipos_forcados = tipos_forcados or {}
    df = df.copy().reset_index(drop=True)
    linhas_originais = len(df)

    novos_nomes, vistos, renomeadas = [], {}, {}
    for original in df.columns:
        novo = padronizar_nome_coluna(renomear.get(str(original), original))
        if novo in vistos:
            vistos[novo] += 1
            novo = f"{novo}_{vistos[novo]}"
        else:
            vistos[novo] = 0
        novos_nomes.append(novo)
        if novo != str(original):
            renomeadas[str(original)] = novo
    df.columns = novos_nomes

    problemas: list[dict] = []
    tipos: dict[str, str] = {}
    for coluna in df.columns:
        tipo = tipos_forcados.get(coluna)
        if tipo not in TIPOS:
            tipo = detectar_tipo(coluna, df[coluna])
        tipos[coluna] = tipo
        df[coluna] = _limpar_coluna(df[coluna], tipo, nome, coluna, problemas)

    df = df.dropna(how="all")
    antes = len(df)
    df = df.drop_duplicates().reset_index(drop=True)

    return ResultadoTabela(
        nome=nome,
        dados=df,
        tipos=tipos,
        problemas=problemas,
        linhas_originais=linhas_originais,
        duplicadas_removidas=antes - len(df),
        renomeadas=renomeadas,
    )


def perfil_tabela(resultado: ResultadoTabela) -> pd.DataFrame:
    """Dicionário de dados: uma linha por coluna com tipo, preenchimento e exemplos."""
    linhas = []
    df = resultado.dados
    for coluna, tipo in resultado.tipos.items():
        serie = df[coluna]
        preenchidos = int(serie.notna().sum())
        linha = {
            "tabela": resultado.nome,
            "coluna": coluna,
            "tipo_detectado": tipo,
            "preenchidos": preenchidos,
            "vazios": int(len(df) - preenchidos),
            "pct_preenchido": round(100 * preenchidos / len(df), 1) if len(df) else 0.0,
            "valores_distintos": int(serie.nunique(dropna=True)),
            "exemplos": ", ".join(str(x) for x in serie.dropna().astype(str).unique()[:3]),
        }
        if tipo in {"valor", "numero"} and preenchidos:
            linha.update(minimo=serie.min(), maximo=serie.max(), soma=serie.sum())
        if tipo == "data" and preenchidos:
            linha.update(minimo=serie.min().date(), maximo=serie.max().date())
        linhas.append(linha)
    return pd.DataFrame(linhas)
