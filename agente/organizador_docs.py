"""Organização de documentos públicos e busca da continuação entre eles.

1. Classifica cada documento (edital, contrato, termo aditivo, empenho, portaria...).
2. Extrai metadados: órgão, número, data, objeto, processos, contratos, licitações,
   empenhos, CNPJs e valores.
3. Liga documentos que se continuam (mesmo processo/contrato/licitação/empenho,
   referências explícitas, partes e páginas de um mesmo arquivo) em "dossiês".
4. Ordena cada dossiê no tempo e aponta lacunas: etapas, aditivos ou páginas que faltam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import validadores as v
from .documentos import Documento

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

# (tipo, etapa no ciclo da contratação pública, padrões no início do documento)
TIPOS_DOCUMENTO = (
    ("Termo aditivo", 6, r"\b(?:\d+[ºo°]?\s*)?termo aditivo\b"),
    ("Termo de apostilamento", 6, r"\bapostilamento\b"),
    ("Edital", 2, r"\bedital\b"),
    ("Termo de referência", 1, r"\btermo de refer[êe]ncia\b|\bestudo t[ée]cnico preliminar\b"),
    ("Ata de registro de preços", 4, r"\bata de registro de pre[çc]os\b"),
    ("Homologação/Adjudicação", 3, r"\bhomologa[çc][ãa]o\b|\badjudica[çc][ãa]o\b|\bresultado (?:final )?(?:da|do) (?:licita|preg)"),
    ("Contrato", 5, r"\bcontrato\b(?! social)"),
    ("Nota de empenho", 7, r"\bnota de empenho\b|\b\d{4}NE\d{4,6}\b"),
    ("Nota fiscal / liquidação", 8, r"\bnota fiscal\b|\bliquida[çc][ãa]o\b|\batesto\b"),
    ("Ordem bancária / pagamento", 9, r"\bordem banc[áa]ria\b|\b\d{4}OB\d{4,6}\b|\bcomprovante de pagamento\b"),
    ("Convênio / termo de fomento", 5, r"\bconv[êe]nio\b|\btermo de (?:fomento|colabora[çc][ãa]o)\b"),
    ("Prestação de contas", 10, r"\bpresta[çc][ãa]o de contas\b"),
    ("Parecer", 0, r"\bparecer\b"),
    ("Nota técnica", 0, r"\bnota t[ée]cnica\b"),
    ("Despacho", 0, r"\bdespacho\b"),
    ("Ofício", 0, r"\bof[íi]cio\b"),
    ("Memorando", 0, r"\bmemorando\b"),
    ("Portaria", 0, r"\bportaria\b"),
    ("Decreto", 0, r"\bdecreto\b"),
    ("Lei", 0, r"\blei (?:complementar |ordin[áa]ria )?n"),
    ("Resolução / Instrução normativa", 0, r"\bresolu[çc][ãa]o\b|\binstru[çc][ãa]o normativa\b"),
    ("Ata de reunião", 0, r"\bata da?\b.*\breuni[ãa]o\b"),
    ("Relatório", 0, r"\brelat[óo]rio\b"),
    ("Requerimento / solicitação", 0, r"\brequerimento\b|\bsolicita[çc][ãa]o\b"),
)

ETAPAS = {
    1: "Planejamento (TR/ETP)", 2: "Edital", 3: "Homologação", 4: "Ata de registro de preços",
    5: "Contrato/Convênio", 6: "Aditivos", 7: "Empenho", 8: "Liquidação/NF", 9: "Pagamento",
    10: "Prestação de contas",
}

ORGAO = re.compile(
    r"^\s*((?:MINIST[ÉE]RIO|SECRETARIA|PREFEITURA|GOVERNO|TRIBUNAL|UNIVERSIDADE|INSTITUTO|"
    r"FUNDA[ÇC][ÃA]O|AG[ÊE]NCIA|C[ÂA]MARA|ASSEMBLEIA|CONTROLADORIA|DEFENSORIA|MINIST[ÉE]RIO P[ÚU]BLICO|"
    r"CONSELHO|COMPANHIA|EMPRESA BRASILEIRA|SUPERINTEND[ÊE]NCIA|DEPARTAMENTO|AUTARQUIA|"
    r"MUNIC[ÍI]PIO|ESTADO)\b[^\n]{0,120})",
    re.IGNORECASE | re.MULTILINE,
)

NUM = r"(?:n[ºo°.]?\s*|n[úu]mero\s*)?"
REFERENCIAS = {
    "processo": re.compile(
        r"(?<!\d)(\d{5}\.\d{6}/\d{4}-\d{2}|\d{4,5}\.\d{3,6}/\d{2,4}(?:-\d{1,2})?|\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?!\d)"
        r"|processo\s*(?:administrativo\s*|SEI\s*)?" + NUM + r"([\w./-]*\d[\w./-]*)",
        re.IGNORECASE,
    ),
    "contrato": re.compile(r"contrato\s*(?:administrativo\s*)?" + NUM + r"(\d{1,6}\s*/\s*\d{2,4})", re.IGNORECASE),
    "licitacao": re.compile(
        r"(?:preg[ãa]o(?: eletr[ôo]nico| presencial)?|concorr[êe]ncia|tomada de pre[çc]os|convite|"
        r"dispensa(?: de licita[çc][ãa]o)?|inexigibilidade|edital|licita[çc][ãa]o)\s*" + NUM + r"(\d{1,6}\s*/\s*\d{2,4})",
        re.IGNORECASE,
    ),
    "ata_srp": re.compile(r"ata de registro de pre[çc]os\s*" + NUM + r"(\d{1,6}\s*/\s*\d{2,4})", re.IGNORECASE),
    "empenho": re.compile(r"\b(\d{4}NE\d{4,6})\b", re.IGNORECASE),
    "ordem_bancaria": re.compile(r"\b(\d{4}OB\d{4,6})\b", re.IGNORECASE),
    "convenio": re.compile(r"(?:conv[êe]nio|termo de fomento|termo de colabora[çc][ãa]o)\s*" + NUM + r"(\d{1,7}\s*/\s*\d{2,4})", re.IGNORECASE),
}
CNPJ = re.compile(r"(?<![\d./-])\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?![\d./-])")
VALOR = re.compile(r"R\$\s*([\d.]+,\d{2})")
DATA_NUMERICA = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
DATA_EXTENSO = re.compile(r"\b(\d{1,2})[º°]?\s+de\s+([a-zç]+)\s+de\s+(\d{4})\b", re.IGNORECASE)
OBJETO = re.compile(r"(?:^|\n)\s*(?:do\s+)?(?:objeto|assunto|ementa)\s*[:.\-–]\s*([^\n]{10,400})", re.IGNORECASE)
ADITIVO_NUM = re.compile(r"\b(\d+)[ºo°]?\s*(?:\(\w+\)\s*)?termo aditivo|termo aditivo\s*" + NUM + r"(\d+)", re.IGNORECASE)
FOLHAS = re.compile(r"\b(?:fls?\.|folhas?)\s*(\d{1,5})\b", re.IGNORECASE)
PARTE_ARQUIVO = re.compile(r"^(.*?)[\s_\-.]*(?:parte|part|vol(?:ume)?|anexo|cont(?:inua[çc][ãa]o)?)[\s_\-.]*(\d+)$", re.IGNORECASE)


@dataclass
class FichaDocumento:
    nome: str
    tipo: str
    etapa: int
    orgao: str = ""
    numero: str = ""
    data: date | None = None
    objeto: str = ""
    referencias: dict[str, set[str]] = field(default_factory=dict)
    cnpjs: set[str] = field(default_factory=set)
    valor_total: float | None = None
    aditivo_numero: int | None = None
    folhas: tuple[int, int] | None = None
    paginas: int = 0
    caracteres: int = 0
    resumo: str = ""


def _normalizar_ref(valor: str) -> str:
    texto = re.sub(r"\s+", "", valor).upper().strip(".,;:-")
    if "/" in texto:
        numero, _, resto = texto.partition("/")
        ano = re.match(r"\d*", resto).group()
        sufixo = resto[len(ano):]
        if len(ano) == 2:
            ano = "20" + ano
        return f"{numero.lstrip('0') or '0'}/{ano}{sufixo}"
    return texto


def _data_principal(texto: str) -> date | None:
    candidatas = []
    for d, m, a in DATA_EXTENSO.findall(texto):
        mes = MESES.get(v.remover_acentos(m.lower()))
        if mes:
            try:
                candidatas.append(date(int(a), mes, int(d)))
            except ValueError:
                pass
    for bruto in DATA_NUMERICA.findall(texto):
        convertida = v.converter_data(bruto)
        if convertida:
            candidatas.append(convertida)
    # A data de assinatura costuma vir no fim; a do cabeçalho, no início. Usa a mais recente
    # entre as plausíveis (descarta datas de nascimento antigas e leis de referência).
    plausiveis = [c for c in candidatas if c.year >= 1990]
    return max(plausiveis) if plausiveis else None


def classificar(texto: str) -> tuple[str, int]:
    """O tipo é o termo que aparece PRIMEIRO no cabeçalho (o título do documento).

    Ex.: "NOTA DE EMPENHO ... referente ao Contrato nº 12" é nota de empenho, não contrato.
    """
    for trecho in (texto[:1500], texto):
        candidatos = []
        for ordem, (tipo, etapa, padrao) in enumerate(TIPOS_DOCUMENTO):
            if m := re.search(padrao, trecho, re.IGNORECASE):
                candidatos.append((m.start(), ordem, tipo, etapa))
        if candidatos:
            _, _, tipo, etapa = min(candidatos)
            return tipo, etapa
    return "Outro", 0


def fichar(doc: Documento) -> FichaDocumento:
    texto = doc.texto
    tipo, etapa = classificar(texto)
    ficha = FichaDocumento(nome=doc.nome, tipo=tipo, etapa=etapa, paginas=len(doc.paginas), caracteres=len(texto))

    if m := ORGAO.search(texto[:3000]):
        ficha.orgao = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    ficha.data = _data_principal(texto)
    if m := OBJETO.search(texto):
        ficha.objeto = re.sub(r"\s+", " ", m.group(1)).strip()

    for chave, padrao in REFERENCIAS.items():
        valores = set()
        for m in padrao.finditer(texto):
            bruto = next((g for g in m.groups() if g), None)
            if bruto and re.search(r"\d", bruto):
                valores.add(_normalizar_ref(bruto))
        if valores:
            ficha.referencias[chave] = valores
    ficha.cnpjs = {v.formatar_cnpj(c) for c in CNPJ.findall(texto) if v.cnpj_valido(c)}

    # Número do próprio documento: primeira ocorrência "<TIPO> nº X/AAAA" no cabeçalho.
    if m := re.search(r"n[ºo°.]\s*([\d.]+\s*/\s*\d{2,4})", texto[:800], re.IGNORECASE):
        ficha.numero = _normalizar_ref(m.group(1))

    if tipo == "Nota de empenho" and ficha.referencias.get("empenho"):
        ficha.numero = sorted(ficha.referencias["empenho"])[0]
    elif tipo == "Ordem bancária / pagamento" and ficha.referencias.get("ordem_bancaria"):
        ficha.numero = sorted(ficha.referencias["ordem_bancaria"])[0]

    valores = [v.converter_valor_monetario(x) for x in VALOR.findall(texto)]
    valores = [x for x in valores if x]
    if valores:
        ficha.valor_total = max(valores)  # valor global do contrato/edital costuma ser o maior

    if tipo == "Termo aditivo" and (m := ADITIVO_NUM.search(texto[:1500])):
        ficha.aditivo_numero = int(next(g for g in m.groups() if g))
        contratos = sorted(ficha.referencias.get("contrato", []))
        ficha.numero = f"{ficha.aditivo_numero}º TA" + (f" ao contrato {contratos[0]}" if contratos else "")

    folhas = [int(x) for x in FOLHAS.findall(texto)]
    if folhas:
        ficha.folhas = (min(folhas), max(folhas))
    return ficha


# ---------- Continuação ----------

class _Uniao:
    def __init__(self, itens):
        self.pai = {i: i for i in itens}

    def achar(self, x):
        while self.pai[x] != x:
            self.pai[x] = self.pai[self.pai[x]]
            x = self.pai[x]
        return x

    def unir(self, a, b):
        self.pai[self.achar(a)] = self.achar(b)


def _parte_arquivo(nome: str) -> tuple[str, int] | None:
    if m := PARTE_ARQUIVO.match(nome):
        return m.group(1).lower().strip(" _-."), int(m.group(2))
    return None


def herdar_partes(fichas: list[FichaDocumento]) -> None:
    """Parte 2, 3... de um arquivo herda tipo, órgão, número e objeto da parte 1."""
    primeiras: dict[str, tuple[int, FichaDocumento]] = {}
    for f in fichas:
        if p := _parte_arquivo(f.nome):
            if p[0] not in primeiras or p[1] < primeiras[p[0]][0]:
                primeiras[p[0]] = (p[1], f)
    for f in fichas:
        if (p := _parte_arquivo(f.nome)) and primeiras[p[0]][1] is not f:
            base = primeiras[p[0]][1]
            f.tipo, f.etapa = base.tipo, base.etapa
            f.orgao = f.orgao or base.orgao
            f.numero = base.numero
            f.objeto = f.objeto or base.objeto
            f.data = f.data or base.data


def encontrar_continuacao(fichas: list[FichaDocumento], documentos: dict[str, Documento]):
    """Agrupa documentos relacionados em dossiês e lista os vínculos encontrados."""
    herdar_partes(fichas)
    nomes = [f.nome for f in fichas]
    uniao = _Uniao(nomes)
    vinculos = []

    indice: dict[tuple[str, str], list[str]] = {}
    for f in fichas:
        for chave, valores in f.referencias.items():
            for valor in valores:
                indice.setdefault((chave, valor), []).append(f.nome)
    for (chave, valor), docs in indice.items():
        for a, b in zip(docs, docs[1:]):
            uniao.unir(a, b)
            vinculos.append({"documento_a": a, "documento_b": b, "motivo": f"Mesmo {chave.replace('_', ' ')} {valor}"})

    # Partes de um mesmo arquivo (relatorio_parte1, relatorio_parte2 / volume 2 / continuação).
    partes: dict[str, list[tuple[int, str]]] = {}
    for nome in nomes:
        if p := _parte_arquivo(nome):
            partes.setdefault(p[0], []).append((p[1], nome))
    for base, lista in partes.items():
        lista.sort()
        for (_, a), (_, b) in zip(lista, lista[1:]):
            uniao.unir(a, b)
            vinculos.append({"documento_a": a, "documento_b": b, "motivo": f"Partes do mesmo arquivo '{base}'"})

    # Texto que termina no meio de uma frase e outro que começa em minúscula, com folhas em sequência.
    for a in fichas:
        for b in fichas:
            if a.nome == b.nome or not (a.folhas and b.folhas):
                continue
            if b.folhas[0] == a.folhas[1] + 1:
                fim = documentos[a.nome].texto.rstrip()[-1:] if documentos[a.nome].texto.strip() else "."
                inicio = documentos[b.nome].texto.lstrip()[:1]
                if fim not in ".!?" or inicio.islower():
                    uniao.unir(a.nome, b.nome)
                    vinculos.append({
                        "documento_a": a.nome, "documento_b": b.nome,
                        "motivo": f"Continuação de folhas ({a.folhas[1]} → {b.folhas[0]})",
                    })

    grupos: dict[str, list[FichaDocumento]] = {}
    for f in fichas:
        grupos.setdefault(uniao.achar(f.nome), []).append(f)

    dossies, lacunas = [], []
    for numero, membros in enumerate(sorted(grupos.values(), key=lambda g: -len(g)), start=1):
        rotulo = f"Dossiê {numero:02d}"
        membros.sort(key=lambda f: (f.data or date.max, f.etapa, f.aditivo_numero or 0, f.nome))
        refs_comuns = _referencia_principal(membros)
        for ordem, f in enumerate(membros, start=1):
            dossies.append({
                "dossie": rotulo,
                "identificacao": refs_comuns,
                "ordem": ordem,
                "documento": f.nome,
                "tipo": f.tipo,
                "etapa": ETAPAS.get(f.etapa, "—"),
                "data": f.data,
                "numero": f.numero,
                "valor": f.valor_total,
            })
        lacunas.extend(_lacunas(rotulo, membros))
    return pd.DataFrame(dossies), pd.DataFrame(vinculos).drop_duplicates(), pd.DataFrame(lacunas)


def _referencia_principal(membros: list[FichaDocumento]) -> str:
    contagem: dict[str, int] = {}
    for f in membros:
        for chave, valores in f.referencias.items():
            for valor in valores:
                rotulo = f"{chave.replace('_', ' ')} {valor}"
                contagem[rotulo] = contagem.get(rotulo, 0) + 1
    if not contagem:
        return membros[0].objeto[:80] or membros[0].nome
    principais = sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    return "; ".join(r for r, _ in principais)


def _lacunas(rotulo: str, membros: list[FichaDocumento]) -> list[dict]:
    """O que deveria existir e não foi enviado — 'a continuação que falta'."""
    achados = []
    etapas = {f.etapa for f in membros if f.etapa}
    if etapas:
        maior = max(etapas)
        tem_contratacao = any(e in etapas for e in (2, 3, 4, 5))
        if tem_contratacao:
            for etapa in (2, 5):
                if etapa < maior and etapa not in etapas:
                    achados.append({"dossie": rotulo, "lacuna": f"Falta documento da etapa '{ETAPAS[etapa]}'"})
        if 7 in etapas and 5 not in etapas and 4 not in etapas:
            achados.append({"dossie": rotulo, "lacuna": "Empenho sem contrato/ata correspondente"})
        if 9 in etapas and 7 not in etapas:
            achados.append({"dossie": rotulo, "lacuna": "Pagamento sem nota de empenho correspondente"})
        if 5 in etapas and maior == 5:
            achados.append({"dossie": rotulo, "lacuna": "Próximo passo esperado: nota de empenho / execução do contrato"})
        if 2 in etapas and maior < 3:
            achados.append({"dossie": rotulo, "lacuna": "Próximo passo esperado: homologação/resultado e contrato"})

    aditivos = sorted(f.aditivo_numero for f in membros if f.aditivo_numero)
    if aditivos:
        faltando = sorted(set(range(1, max(aditivos) + 1)) - set(aditivos))
        for n in faltando:
            achados.append({"dossie": rotulo, "lacuna": f"Falta o {n}º termo aditivo (existe até o {max(aditivos)}º)"})

    # Folhas só são comparáveis dentro do mesmo processo (ou do mesmo arquivo em partes).
    por_autos: dict[str, list[tuple[int, int]]] = {}
    for f in membros:
        if not f.folhas:
            continue
        parte = _parte_arquivo(f.nome)
        processos = sorted(f.referencias.get("processo", []))
        autos = f"arquivo {parte[0]}" if parte else (f"processo {processos[0]}" if processos else None)
        if autos:
            por_autos.setdefault(autos, []).append(f.folhas)
    for autos, folhas in por_autos.items():
        folhas.sort()
        for (_, a_fim), (b_ini, _) in zip(folhas, folhas[1:]):
            if b_ini > a_fim + 1:
                achados.append({"dossie": rotulo, "lacuna": f"Faltam as folhas {a_fim + 1} a {b_ini - 1} ({autos})"})

    partes = sorted(p[1] for f in membros if (p := _parte_arquivo(f.nome)))
    if partes:
        for n in sorted(set(range(1, max(partes) + 1)) - set(partes)):
            achados.append({"dossie": rotulo, "lacuna": f"Falta a parte {n} do arquivo"})
    return achados


def tabela_fichas(fichas: list[FichaDocumento]) -> pd.DataFrame:
    linhas = []
    for f in fichas:
        linhas.append({
            "documento": f.nome,
            "tipo": f.tipo,
            "etapa": ETAPAS.get(f.etapa, "—"),
            "orgao": f.orgao,
            "numero": f.numero,
            "data": f.data,
            "objeto": f.objeto,
            "valor_maior_citado": f.valor_total,
            "processos": ", ".join(sorted(f.referencias.get("processo", []))),
            "contratos": ", ".join(sorted(f.referencias.get("contrato", []))),
            "licitacoes": ", ".join(sorted(f.referencias.get("licitacao", []))),
            "empenhos": ", ".join(sorted(f.referencias.get("empenho", []))),
            "cnpjs": ", ".join(sorted(f.cnpjs)),
            "folhas": f"{f.folhas[0]}–{f.folhas[1]}" if f.folhas else "",
            "paginas": f.paginas,
            "resumo": f.resumo,
        })
    return pd.DataFrame(linhas)
