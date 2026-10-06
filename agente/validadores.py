"""Validação e formatação de identificadores e valores usados em dados do governo."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}

NOMES_UF = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA",
    "CEARA": "CE", "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO",
    "MARANHAO": "MA", "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG",
    "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE", "PIAUI": "PI",
    "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO", "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP",
    "SERGIPE": "SE", "TOCANTINS": "TO",
}


def remover_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def somente_digitos(valor: str) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def cpf_valido(valor: str) -> bool:
    cpf = somente_digitos(valor).zfill(11)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(cpf[tamanho]):
            return False
    return True


def cnpj_valido(valor: str) -> bool:
    cnpj = somente_digitos(valor).zfill(14)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for tamanho in (12, 13):
        soma = sum(int(d) * p for d, p in zip(cnpj[:tamanho], pesos[13 - tamanho:]))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if digito != int(cnpj[tamanho]):
            return False
    return True


def formatar_cpf(valor: str) -> str:
    d = somente_digitos(valor).zfill(11)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def formatar_cnpj(valor: str) -> str:
    d = somente_digitos(valor).zfill(14)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def formatar_cep(valor: str) -> str | None:
    d = somente_digitos(valor)
    if not d or len(d) > 8:
        return None
    d = d.zfill(8)
    return f"{d[:5]}-{d[5:]}"


def normalizar_uf(valor: str) -> str | None:
    texto = remover_acentos(str(valor or "")).strip().upper()
    if texto in UFS:
        return texto
    return NOMES_UF.get(texto)


def converter_valor_monetario(valor: str) -> float | None:
    """Converte 'R$ 1.234,56', '1234.56', '(1.000,00)' em float."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    negativo = texto.startswith("(") and texto.endswith(")") or texto.startswith("-")
    texto = re.sub(r"[^\d,.]", "", texto)
    if not texto:
        return None
    if "," in texto and "." in texto:
        # O último separador é o decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return -numero if negativo else numero


FORMATOS_DATA = (
    "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%Y%m%d",
)


def converter_data(valor: str) -> date | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    texto = texto.split(".")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", texto) else texto
    for formato in FORMATOS_DATA:
        try:
            convertida = datetime.strptime(texto, formato).date()
        except ValueError:
            continue
        if 1900 <= convertida.year <= 2100:
            return convertida
    return None
