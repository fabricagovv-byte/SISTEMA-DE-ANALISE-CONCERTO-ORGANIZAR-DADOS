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

EXTENSOES_SUPORTADAS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json", ".jsonl", ".ndjson", ".xml",
                        ".db", ".sqlite", ".sqlite3", ".sql"}
CODIFICACOES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
LINHA_TOTAL = re.compile(r"^\s*(total|totais|subtotal|soma|somat[óo]rio|tot\.)\b", re.IGNORECASE)


def decodificar(conteudo: bytes) -> tuple[str, str]:
    if conteudo[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return conteudo.decode("utf-16"), "UTF-16"
    if len(conteudo) > 4 and conteudo[1:4:2] == b"\x00\x00" and conteudo[0:4:2] != b"\x00\x00":
        return conteudo.decode("utf-16-le"), "UTF-16 (sem BOM)"
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
    info = {"formato": "CSV", "codificacao": codificacao, "separador": "TAB" if separador == "\t" else separador}
    df = _limpar(df, info)
    df.attrs["leitura"] = info
    return df


def _textual(v):
    # Tudo vira texto já aqui: evita que IDs 2 virem 2.0 quando a coluna tem vazios.
    if isinstance(v, dict):
        return {k: _textual(x) for k, x in v.items()}
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    return None if v is None else str(v)


def _registros_para_df(dados: list, info: dict) -> pd.DataFrame:
    esquemas = {tuple(sorted(d)) for d in dados if isinstance(d, dict)}
    tipos_id: dict[str, set[str]] = {}
    for d in dados:
        if isinstance(d, dict):
            for k, x in d.items():
                if re.search(r"id$|^id|_id", k, re.IGNORECASE) and x is not None:
                    tipos_id.setdefault(k, set()).add("texto" if isinstance(x, str) else "número")
    df = pd.json_normalize([_textual(d) for d in dados if isinstance(d, dict)])
    df = df.map(lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))
    info["esquemas_diferentes"] = len(esquemas)
    if len({t for ts in tipos_id.values() for t in ts}) > 1:
        info["ids_tipos_misturados"] = {k: sorted(t) for k, t in tipos_id.items()}
    return _limpar(df, info)


def _anexos_base64(dados, caminho="") -> list[tuple[str, str]]:
    """Procura textos em base64 que, decodificados, são CSV (anexos escondidos dentro do JSON)."""
    import base64

    achados = []
    if isinstance(dados, dict):
        for k, x in dados.items():
            achados += _anexos_base64(x, f"{caminho}.{k}" if caminho else k)
    elif isinstance(dados, list):
        for i, x in enumerate(dados[:1000]):
            achados += _anexos_base64(x, f"{caminho}[{i}]")
    elif isinstance(dados, str) and len(dados) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", dados):
        try:
            texto = base64.b64decode(dados, validate=False).decode("utf-8-sig")
        except Exception:
            return achados
        linhas = [l for l in texto.splitlines() if l.strip()]
        if len(linhas) >= 2 and any(sep in linhas[0] for sep in ",;\t|"):
            achados.append((caminho, texto))
    return achados


def _ler_json(conteudo: bytes, base: str) -> dict[str, pd.DataFrame]:
    texto, codificacao = decodificar(conteudo)
    original = json.loads(texto)
    dados = original
    if isinstance(dados, dict):
        # Formato comum de APIs: {"dados": [...]} ou {"registros": [...]}
        listas = [v for v in dados.values() if isinstance(v, list)]
        dados = listas[0] if listas else [dados]
    info = {"formato": "JSON", "codificacao": codificacao}
    df = _registros_para_df(dados, info)
    df.attrs["leitura"] = info
    tabelas = {base: df}
    for campo, csv_texto in _anexos_base64(original):
        anexo = _ler_csv(csv_texto.encode("utf-8"))
        anexo.attrs["leitura"].update({"formato": "CSV em base64 dentro do JSON", "campo": campo})
        tabelas[f"{base}__anexo_{re.sub(r'[^a-z0-9]+', '_', campo.lower()).strip('_')}"] = anexo
    return tabelas


def _ler_jsonl(conteudo: bytes) -> pd.DataFrame:
    texto, codificacao = decodificar(conteudo)
    dados, quebradas = [], []
    for n, linha in enumerate(texto.splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            dados.append(json.loads(linha))
        except json.JSONDecodeError:
            quebradas.append(n)
    info = {"formato": "JSONL", "codificacao": codificacao}
    if quebradas:
        info["linhas_json_quebradas"] = quebradas
    df = _registros_para_df(dados, info)
    df.attrs["leitura"] = info
    return df


def _ler_xml(conteudo: bytes) -> pd.DataFrame:
    import xml.etree.ElementTree as ET
    from collections import Counter

    raiz = ET.fromstring(conteudo)  # respeita o encoding declarado (ex.: ISO-8859-1)
    codificacao = re.search(rb'encoding=["\']([\w-]+)', conteudo[:200])
    filhos = list(raiz)
    tag = Counter(f.tag for f in filhos).most_common(1)[0][0] if filhos else None

    def achatar(el, prefixo=""):
        linha = {f"{prefixo}{k}": v for k, v in el.attrib.items()}
        for filho in el:
            nome = f"{prefixo}{filho.tag}"
            if len(filho) or filho.attrib:
                linha.update(achatar(filho, nome + "_"))
                if (filho.text or "").strip():
                    linha[nome] = filho.text.strip()
            else:
                linha[nome] = (filho.text or "").strip() or None
        return linha

    registros = [achatar(f) for f in filhos if f.tag == tag]
    info = {"formato": "XML", "codificacao": codificacao.group(1).decode() if codificacao else "UTF-8", "registro": tag}
    df = _limpar(pd.DataFrame(registros), info)
    df.attrs["leitura"] = info
    return df


def _ler_sqlite(conteudo: bytes, base: str) -> dict[str, pd.DataFrame]:
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.deserialize(conteudo)
    tabelas = {}
    for (nome,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cursor = con.execute(f'SELECT * FROM "{nome}"')
        colunas = [c[0] for c in cursor.description]
        linhas = [[None if x is None else _celula_texto(x) for x in linha] for linha in cursor.fetchall()]
        info = {"formato": "SQLite", "tabela": nome}
        df = _limpar(pd.DataFrame(linhas, columns=colunas), info)
        df.attrs["leitura"] = info
        tabelas[f"{base}__{nome}"] = df
    con.close()
    return tabelas


def _valores_sql(texto: str) -> list[list]:
    """Tuplas de um VALUES (...),(...) com strings entre aspas simples, números e NULL."""
    tuplas, atual, campo, i, dentro, aspas = [], [], "", 0, False, False
    while i < len(texto):
        c = texto[i]
        if aspas:
            if c == "\\" and i + 1 < len(texto):
                campo += texto[i + 1]
                i += 1
            elif c == "'" and texto[i + 1:i + 2] == "'":
                campo += "'"
                i += 1
            elif c == "'":
                aspas = False
            else:
                campo += c
        elif c == "'":
            aspas, campo = True, campo or ""
        elif c == "(" and not dentro:
            dentro, atual, campo = True, [], ""
        elif c == "," and dentro:
            atual.append(campo.strip())
            campo = ""
        elif c == ")" and dentro:
            atual.append(campo.strip())
            tuplas.append([None if x.upper() == "NULL" else x for x in atual])
            dentro, campo = False, ""
        elif dentro:
            campo += c
        i += 1
    return tuplas


def _ler_sql(conteudo: bytes, base: str) -> dict[str, pd.DataFrame]:
    texto, codificacao = decodificar(conteudo)
    colunas: dict[str, list[str]] = {}
    for m in re.finditer(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\((.*?)\)\s*;", texto, re.I | re.S):
        defs = re.split(r",(?![^()]*\))", m.group(2))
        colunas[m.group(1)] = [d.strip().split()[0].strip('`"') for d in defs
                               if d.strip() and not re.match(r"(PRIMARY|KEY|UNIQUE|CONSTRAINT|INDEX|FOREIGN)\b", d.strip(), re.I)]
    linhas: dict[str, list] = {}
    for m in re.finditer(r"INSERT INTO\s+[`\"]?(\w+)[`\"]?\s*(\(([^)]*)\))?\s*VALUES\s*(.*?);\s*$", texto, re.I | re.S | re.M):
        tabela = m.group(1)
        if m.group(3) and tabela not in colunas:
            colunas[tabela] = [c.strip(' `"') for c in m.group(3).split(",")]
        linhas.setdefault(tabela, []).extend(_valores_sql(m.group(4)))
    tabelas = {}
    for tabela, valores in linhas.items():
        nomes = colunas.get(tabela) or [f"coluna_{i + 1}" for i in range(len(valores[0]))]
        info = {"formato": "dump SQL", "codificacao": codificacao, "tabela": tabela}
        df = _limpar(pd.DataFrame([v[:len(nomes)] + [None] * (len(nomes) - len(v)) for v in valores], columns=nomes), info)
        df.attrs["leitura"] = info
        tabelas[f"{base}__{tabela}"] = df
    if not tabelas:
        raise ValueError("dump SQL sem INSERTs reconhecíveis")
    return tabelas


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

    if conteudo[:16] == b"SQLite format 3\x00":
        return _ler_sqlite(conteudo, base)
    if extensao in {".csv", ".tsv", ".txt"}:
        return {base: _ler_csv(conteudo)}
    if extensao == ".json":
        return _ler_json(conteudo, base)
    if extensao in {".jsonl", ".ndjson"}:
        return {base: _ler_jsonl(conteudo)}
    if extensao == ".xml":
        return {base: _ler_xml(conteudo)}
    if extensao == ".sql":
        return _ler_sql(conteudo, base)
    if extensao in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("arquivo .db não é um banco SQLite válido")

    abas, ocultas, formulas = _ler_excel_bruto(conteudo, extensao)
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
        if aba in ocultas:
            info["aba_oculta"] = True
        if formulas.get(aba):
            info.update(formulas[aba])
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


def _avaliar_formula(formula: str, valor_de) -> float | None:
    """Avalia fórmulas simples (=D3*E3, =A1+B1, =SUM(F3:F9)) quando o arquivo não guardou o resultado."""
    from openpyxl.utils import column_index_from_string, get_column_letter

    expr = formula.lstrip("=").upper()

    def soma(m):
        c1, l1, c2, l2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        total = 0.0
        for col in range(column_index_from_string(c1), column_index_from_string(c2) + 1):
            for lin in range(l1, l2 + 1):
                x = valor_de(f"{get_column_letter(col)}{lin}")
                total += x if isinstance(x, (int, float)) else 0
        return repr(total)

    expr = re.sub(r"SUM\(\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)\)", soma, expr)
    def ref(m):
        x = valor_de(m.group(1) + m.group(2))
        return repr(float(x)) if isinstance(x, (int, float)) else "None"
    expr = re.sub(r"\$?([A-Z]{1,3})\$?(\d+)", ref, expr)
    if "None" in expr or not re.fullmatch(r"[\d.+\-*/() e]+", expr):
        return None
    try:
        return round(float(eval(expr, {"__builtins__": {}}, {})), 10)  # noqa: S307 — só números e operadores
    except Exception:
        return None


def _ler_excel_bruto(conteudo: bytes, extensao: str):
    """Lê todas as abas (inclusive ocultas), calcula fórmulas sem resultado salvo e
    remove linhas que são só uma fórmula de SOMA (total no meio dos dados)."""
    if extensao == ".xls":
        return pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, dtype=object), set(), {}
    import openpyxl

    wb_f = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=False)
    wb_v = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    abas, ocultas, formulas = {}, set(), {}
    for ws in wb_f.worksheets:
        if ws.sheet_state != "visible":
            ocultas.add(ws.title)
        valores = wb_v[ws.title]
        calculadas, somas = 0, []

        def valor_de(coord, ws=ws, valores=valores):
            x = valores[coord].value
            f = ws[coord].value
            if x is None and isinstance(f, str) and f.startswith("="):
                return _avaliar_formula(f, valor_de)
            return x

        grade = []
        for linha in ws.iter_rows():
            vals, so_soma = [], True
            preenchidas = 0
            for celula in linha:
                f = celula.value
                x = valores[celula.coordinate].value
                if isinstance(f, str) and f.startswith("="):
                    if x is None:
                        x = _avaliar_formula(f, valor_de)
                        calculadas += x is not None
                    so_soma &= "SUM(" in f.upper() or "SOMA(" in f.upper()
                    preenchidas += 1
                elif f is not None and str(f).strip():
                    so_soma = False
                    preenchidas += 1
                vals.append(x)
            if preenchidas and so_soma:
                somas.append(linha[0].row)
                continue
            grade.append(vals)
        df = pd.DataFrame(grade, dtype=object)
        df.index = [i for i in range(1, ws.max_row + 1) if i not in somas][:len(df)]
        df.index = df.index - 1
        abas[ws.title] = df
        extra = {}
        if calculadas:
            extra["formulas_calculadas"] = calculadas
        if somas:
            extra["linhas_soma_removidas"] = somas
        if ws.merged_cells.ranges:
            extra["celulas_mescladas"] = [str(r) for r in ws.merged_cells.ranges][:5]
        formulas[ws.title] = extra
    return abas, ocultas, formulas


def _celula_texto(x) -> str:
    if hasattr(x, "isoformat"):
        return x.isoformat(sep=" ") if hasattr(x, "hour") else x.isoformat()
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def ler_caminho(caminho: str | Path) -> dict[str, pd.DataFrame]:
    caminho = Path(caminho)
    return ler_arquivo(caminho.name, caminho.read_bytes())
