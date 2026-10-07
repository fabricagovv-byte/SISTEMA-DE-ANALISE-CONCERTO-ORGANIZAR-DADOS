"""Detecção e anonimização de dados pessoais e sensíveis (LGPD — Lei 13.709/2018).

Categorias:
- "pessoal"   : identifica uma pessoa (art. 5º, I) — CPF, RG, e-mail, telefone, endereço...
- "sensivel"  : dado pessoal sensível (art. 5º, II e art. 11) — saúde, raça/cor, religião,
                opinião política, filiação sindical, vida sexual, biometria/genética.
- "crianca"   : indício de dado de criança/adolescente (art. 14).
- "empresa"   : identificador de pessoa jurídica (CNPJ) — público, listado só para cruzamento.
- "credencial": senha, chave de API, segredo, string de conexão — nunca deveria estar em arquivo aberto.
- "financeiro": número de cartão de pagamento (validado pelo algoritmo de Luhn) — PCI-DSS.
- "infraestrutura": endereço IP interno de servidor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from . import validadores as v


@dataclass(frozen=True)
class Regra:
    tipo: str
    categoria: str
    padrao: re.Pattern
    validar: object = None  # função(str) -> bool
    grupo: int = 0


def _pis_valido(valor: str) -> bool:
    d = v.somente_digitos(valor)
    if len(d) != 11 or d == d[0] * 11:
        return False
    soma = sum(int(a) * b for a, b in zip(d[:10], (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)))
    digito = 11 - soma % 11
    return (0 if digito >= 10 else digito) == int(d[10])


def _cns_valido(valor: str) -> bool:
    d = v.somente_digitos(valor)
    if len(d) != 15 or d[0] not in "12789":
        return False
    return sum(int(a) * (15 - i) for i, a in enumerate(d)) % 11 == 0


def _cartao_valido(valor: str) -> bool:
    d = v.somente_digitos(valor)
    if not 13 <= len(d) <= 19 or d[0] not in "23456" or (len(d) == 14 and v.cnpj_valido(d)):
        return False
    return v.luhn_valido(d)


def _f(padrao: str) -> re.Pattern:
    return re.compile(padrao, re.IGNORECASE)


CTX = r"\s*(?:n[ºo°.]|n[úu]mero)?\s*:?\s*"

REGRAS: tuple[Regra, ...] = (
    Regra("CPF", "pessoal", _f(r"(?<![\d./-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d./-])"), v.cpf_valido),
    Regra("CNPJ", "empresa", _f(r"(?<![\d./-])\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?![\d./-])"), v.cnpj_valido),
    Regra("PIS/NIS/PASEP", "pessoal", _f(r"(?:PIS|NIS|PASEP|NIT)[^\d]{0,15}(\d{3}\.?\d{5}\.?\d{2}-?\d)"), _pis_valido, 1),
    Regra("Cartão SUS (CNS)", "sensivel", _f(r"(?<!\d)([1-2789]\d{2}\s?\d{4}\s?\d{4}\s?\d{4})(?!\d)"), _cns_valido, 1),
    Regra("RG", "pessoal", _f(r"\b(?:RG|R\.G\.|identidade|carteira de identidade)" + CTX + r"(\d[\dXx.\-]{4,13}(?:\s?-?\s?(?:SSP|SDS|DETRAN|IFP|PC)\s?/?\s?[A-Z]{2})?)"), None, 1),
    Regra("Nome de pessoa", "pessoal", re.compile(
        r"(?:\b(?:[Ss]r\.?|[Ss]ra\.?|[Ss]enhora?|[Rr]epresentad[oa] por|[Ss]ervidora?|[Bb]enefici[áa]ri[oa]|"
        r"[Rr]equerente|[Ii]nteressad[oa]|[Pp]aciente|[Ll]igar (?:p/|para|pr[oa])|[Ff]alar com|[Cc]ontato\s*:?|[Cc]liente\s*:|[Nn]ome(?: completo)?\s*:|[Ff]ilh[oa] de|[Mm][ãa]e\s*:|[Pp]ai\s*:)\s+)"
        r"([A-ZÀ-Ú][a-zà-ú]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][a-zà-ú]+){1,5})"), None, 1),
    Regra("CNH", "pessoal", _f(r"\b(?:CNH|habilita[çc][ãa]o)" + CTX + r"(\d{9,11})\b"), None, 1),
    Regra("Título de eleitor", "pessoal", _f(r"t[íi]tulo de eleitor" + CTX + r"(\d{4}\s?\d{4}\s?\d{4})"), None, 1),
    Regra("Passaporte", "pessoal", _f(r"passaporte" + CTX + r"([A-Z]{2}\d{6})\b"), None, 1),
    Regra("E-mail", "pessoal", _f(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    Regra("Telefone", "pessoal", _f(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)")),
    Regra("Conta bancária", "pessoal", _f(r"\b(?:ag[êe]ncia|ag\.)\s*:?\s*\d{3,5}(?:-\d)?\s*[,/;]?\s*(?:c/?c|conta(?: corrente)?)\s*:?\s*\d{3,12}-?[\dXx]?")),
    Regra("Data de nascimento", "pessoal", _f(r"(?:nascid[oa] em|data de nascimento|nascimento|DN)\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})"), None, 1),
    Regra("Endereço", "pessoal", _f(r"\b(?:rua|r\.|avenida|av\.|travessa|tv\.|alameda|rodovia|estrada|quadra|qd\.)\s+[A-ZÀ-Ú0-9][^\n,;]{2,60},?\s*(?:n[ºo°.]?\s*)?\d{1,5}")),
    Regra("CEP", "pessoal", _f(r"\bCEP\s*:?\s*\d{5}-?\d{3}\b")),
    Regra("Placa de veículo", "pessoal", re.compile(r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b")),
    # Dados sensíveis (art. 5º, II) — indícios por termos.
    Regra("Saúde: CID", "sensivel", _f(r"\bCID(?:-?10)?\s*:?\s*[A-TV-Z]\d{2}(?:\.\d)?\b")),
    Regra("Saúde", "sensivel", _f(r"\b(?:diagn[óo]stico|laudo m[ée]dico|atestado m[ée]dico|prontu[áa]rio|HIV|AIDS|soropositiv\w*|c[âa]ncer|neoplasia|transtorno mental|esquizofrenia|depress[ãa]o|dependente qu[íi]mico|gestante|gravidez|gr[áa]vida|alergi\w*|afastad[oa] por|licen[çc]a m[ée]dica|defici[êe]ncia (?:f[íi]sica|mental|visual|auditiva|intelectual)|PcD|autis\w+|TEA\b)")),
    Regra("Origem racial ou étnica", "sensivel", _f(r"\b(?:ra[çc]a/cor|cor/ra[çc]a|ra[çc]a|etnia|ind[íi]gena|quilombola)\s*:?\s*(?:preta|parda|branca|amarela|ind[íi]gena|negra|\w+)?")),
    Regra("Convicção religiosa", "sensivel", _f(r"\b(?:religi[ãa]o|cren[çc]a religiosa|cat[óo]lic[oa]|evang[ée]lic[oa]|esp[íi]rita|umbanda|candombl[ée]|mu[çc]ulman[oa]|jud[ae]u|testemunha de jeov[áa])\b")),
    Regra("Opinião política / filiação partidária", "sensivel", _f(r"\b(?:filia[çc][ãa]o partid[áa]ria|filiad[oa] ao partido|militante)\b")),
    Regra("Filiação sindical", "sensivel", _f(r"\b(?:filia[çc][ãa]o sindical|sindicalizad[oa]|filiad[oa] ao sindicato)\b")),
    Regra("Vida sexual / orientação", "sensivel", _f(r"\b(?:orienta[çc][ãa]o sexual|homossexual|bissexual|transexual|identidade de g[êe]nero|nome social)\b")),
    Regra("Biometria / genética", "sensivel", _f(r"\b(?:biometria|impress[ãa]o digital|reconhecimento facial|dados gen[ée]ticos|DNA)\b")),
    # Credenciais e segredos
    Regra("Chave privada", "credencial", _f(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    Regra("Chave de API", "credencial", re.compile(
        r"\b((?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|"
        r"xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{35})\b"), None, 1),
    Regra("Chave de API", "credencial", _f(r"\b(?:api[_-]?key|apikey|chave[_ ](?:de[_ ])?api|access[_-]?key)\w*\s*[:=]\s*([^\s,;]+)"), None, 1),
    Regra("Senha/segredo em texto puro", "credencial", _f(
        r"\b(?:senha|password|passwd|pwd|pass|secret|segredo|token|[a-z_]*_(?:password|secret|token|pass))\b\s*[:=]\s*([^\s,;]+)"), None, 1),
    Regra("Usuário e senha (par)", "credencial", _f(
        r"(?m)^[ \t]*(?:admin|root|administrador|user|usu[áa]rio|login|sa)[ \t]*/[ \t]*(\S+)[ \t]*$"), None, 1),
    Regra("String de conexão com senha", "credencial", _f(r"\b[a-z][a-z0-9+.-]*://[^:\s/]+:([^@\s]+)@"), None, 1),
    Regra("CVV de cartão", "financeiro", _f(r"\b(?:cvv|cvc|c[óo]d(?:igo)?\.? de seguran[çc]a)\s*:?\s*(\d{3,4})\b"), None, 1),
    Regra("Usuário e senha (par)", "credencial", _f(r"\b(?:admin|root|administrador)\s*/\s*([^\s.,;]+)"), None, 1),
    Regra("Usuário de sistema", "credencial", _f(r"\b(?:user|usu[áa]rio|login|username)\s*[:=]\s*([^\s,;]+)"), None, 1),
    Regra("IP interno de servidor", "infraestrutura", re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    Regra("Endereço IP", "pessoal", re.compile(
        r"\b(?!(?:10|127)\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    Regra("Cartão de crédito", "financeiro", re.compile(r"(?<![\d.\-/])((?:\d[ \-]?){12,18}\d)(?![\d.\-/])"), _cartao_valido, 1),
    Regra("Criança/adolescente", "crianca", _f(r"\b(?:menor de idade|crian[çc]a|adolescente|rec[ée]m-nascid[oa]|ECA\b|tutelad[oa]|guarda (?:provis[óo]ria|definitiva))")),
)


TIPOS_OCULTOS = {r.tipo for r in REGRAS if r.categoria in {"credencial", "sensivel"}} | {"Senha em texto puro"}


def mascarar(tipo: str, valor: str) -> str:
    """Máscara no padrão do Portal da Transparência (CPF ***.123.456-**)."""
    d = v.somente_digitos(valor)
    if tipo == "CPF" and len(d) == 11:
        return f"***.{d[3:6]}.{d[6:9]}-**"
    if tipo == "CNPJ":
        return v.formatar_cnpj(valor)  # CNPJ é público
    if tipo == "Telefone" and len(d) >= 8:
        return f"(**) *****-{d[-4:]}"
    if tipo == "Cartão de crédito":
        return f"**** **** **** {d[-4:]}"
    if tipo in TIPOS_OCULTOS:
        return "[oculto]"
    if tipo == "E-mail" and "@" in valor:
        usuario, dominio = valor.split("@", 1)
        return f"{usuario[:2]}***@{dominio}"
    if len(valor) <= 4:
        return "***"
    return valor[:2] + "*" * (len(valor) - 4) + valor[-2:]


def _contexto(linhas_originais: list[str], linhas_anonimas: list[str], inicio: int, conteudo: str) -> str:
    """Linha onde está o achado, tirada do texto JÁ anonimizado por inteiro.

    Anonimizar a página toda antes de recortar garante que nenhum dado vizinho vaze
    (regras que dependem da linha inteira, como "admin / senha", continuam funcionando).
    """
    n = conteudo.count("\n", 0, inicio)
    if len(linhas_anonimas) == len(linhas_originais):
        linha = linhas_anonimas[n]
    else:  # alguma regra juntou linhas: anonimiza só a linha do achado
        linha = anonimizar_texto(linhas_originais[n])
    if len(linha) > 200:
        posicao = inicio - (conteudo.rfind("\n", 0, inicio) + 1)
        centro = int(posicao / max(len(linhas_originais[n]), 1) * len(linha))
        linha = linha[max(0, centro - 100): centro + 100]
        linha = " ".join(linha.split(" ")[1:-1]) or linha
    linha = re.sub(r"\d[\d.\-/ ]{4,}\d", lambda m: "#" * len(m.group()), linha.strip())
    return linha


def encontrar_em_texto(texto: str, documento: str, paginas: list[str] | None = None) -> list[dict]:
    paginas = paginas or [texto]
    achados, vistos = [], set()
    for numero_pagina, conteudo in enumerate(paginas, start=1):
        linhas_originais = conteudo.split("\n")
        linhas_anonimas = anonimizar_texto(conteudo).split("\n")
        for regra in REGRAS:
            for m in regra.padrao.finditer(conteudo):
                valor = m.group(regra.grupo).strip()
                if regra.validar and not regra.validar(valor):
                    continue
                if regra.tipo == "Telefone" and (v.cpf_valido(valor) or len(v.somente_digitos(valor)) < 10):
                    continue
                chave = (regra.tipo, valor.lower(), numero_pagina)
                if chave in vistos:
                    continue
                vistos.add(chave)
                achados.append({
                    "origem": documento,
                    "pagina_ou_coluna": numero_pagina,
                    "tipo": regra.tipo,
                    "categoria": regra.categoria,
                    "valor_mascarado": mascarar(regra.tipo, valor),
                    "contexto": _contexto(linhas_originais, linhas_anonimas, m.start(), conteudo),
                    "_valor": valor,
                })
    return achados


def anonimizar_texto(texto: str) -> str:
    """Substitui dados pessoais por marcadores ([CPF], [E-MAIL]...). CNPJ é mantido (dado público)."""
    resultado = texto
    for regra in REGRAS:
        if regra.categoria == "empresa":
            continue

        def trocar(m, regra=regra):
            valor = m.group(regra.grupo)
            if valor.startswith("["):  # já anonimizado por outra regra
                return m.group(0)
            if regra.validar and not regra.validar(valor):
                return m.group(0)
            if regra.tipo == "Telefone" and len(v.somente_digitos(valor)) < 10:
                return m.group(0)
            marcador = f"[{regra.tipo.upper()}]"
            if regra.categoria == "sensivel" and regra.grupo == 0:
                return marcador
            return m.group(0).replace(valor, marcador)

        resultado = regra.padrao.sub(trocar, resultado)
    return resultado


# ---------- Tabelas ----------

COLUNAS_PESSOAIS = {
    "nome": ("nome", "nm_", "beneficiario", "favorecido", "servidor", "titular", "responsavel", "mae", "pai"),
    "endereco": ("endereco", "logradouro", "rua", "bairro", "complemento"),
    "contato": ("email", "e_mail", "telefone", "celular", "fone", "whatsapp"),
    "documento": ("rg", "identidade", "cnh", "titulo_eleitor", "nis", "pis", "pasep", "passaporte", "cns", "cartao_sus"),
    "nascimento": ("nascimento", "nasc", "dt_nasc_", "data_nasc_", "idade"),
    "bancario": ("agencia", "conta", "banco", "pix"),
}
COLUNAS_SENSIVEIS = {
    "saude": ("cid", "doenca", "diagnostico", "deficiencia", "pcd", "laudo", "gestante", "saude"),
    "raca_cor": ("raca", "cor_raca", "etnia", "raca_cor"),
    "religiao": ("religiao",),
    "orientacao_sexual": ("orientacao_sexual", "identidade_genero", "nome_social"),
    "politica_sindical": ("partido", "filiacao", "sindicato"),
    "biometria": ("biometria", "digital", "foto"),
}


COLUNAS_CREDENCIAIS = {"senha", "password", "pass", "pwd", "passwd", "senha_acesso", "token", "api_key", "secret", "hash_senha"}
COLUNAS_CARTAO = {"cartao", "card", "numero_cartao", "cartao_credito", "cc", "num_cartao", "credit_card"}


def _classificar_coluna(coluna: str, tipo: str) -> tuple[str, str] | None:
    base = re.sub(r"_\d+$", "", coluna)
    if base in COLUNAS_CREDENCIAIS or base.startswith(("senha", "password", "passwd")) or base.endswith(("_senha", "_password")):
        return ("Senha (hash) exposta" if re.search(r"md5|sha|hash", base) else "Senha em texto puro"), "credencial"
    if base in COLUNAS_CARTAO or base.startswith(("cartao_", "card_number")) or base in {"card_number", "numero_do_cartao"}:
        return "Cartão de crédito", "financeiro"
    if re.search(r"salari|remunera|vencimento_bruto|proventos|comiss", base):
        return "Salário/remuneração", "pessoal"
    if re.search(r"dependente|filh[oa]s?\b|menor", base):
        return "Dependentes menores (criança/adolescente)", "crianca"
    if tipo == "cpf":
        return "CPF", "pessoal"
    if tipo == "cpf_cnpj":
        return "CPF/CNPJ", "pessoal"
    if tipo == "cep":
        return "CEP", "pessoal"
    partes = set(coluna.split("_"))
    def casa(p: str) -> bool:
        if p.endswith("_"):
            return coluna.startswith(p)
        return p in partes or (len(p) > 5 and p in coluna)

    for nome, pistas in COLUNAS_SENSIVEIS.items():
        if any(casa(p) for p in pistas):
            return nome, "sensivel"
    for nome, pistas in COLUNAS_PESSOAIS.items():
        if any(casa(p) for p in pistas):
            if nome == "nome" and any(x in coluna for x in ("orgao", "municipio", "empresa", "razao", "programa", "unidade")):
                return None
            return nome, "pessoal"
    return None


def encontrar_em_tabela(nome_tabela: str, df: pd.DataFrame, tipos: dict[str, str]) -> list[dict]:
    achados = []
    for coluna in df.columns:
        classes = []
        classe = _classificar_coluna(coluna, tipos.get(coluna, "texto"))
        if classe:
            classes.append(classe)
        elif tipos.get(coluna) == "texto":
            # Conteúdo de colunas de texto livre (observações, chave/valor de configuração...).
            amostra = df[coluna].dropna().astype(str).head(500)
            for regra in REGRAS:
                if regra.tipo in {"Nome de pessoa", "Endereço", "Placa de veículo"} or not len(amostra):
                    continue
                hits = amostra.map(lambda x, regra=regra: any(
                    (not regra.validar or regra.validar(m.group(regra.grupo))) for m in regra.padrao.finditer(x)
                ))
                # Credencial, cartão, saúde, criança: basta UMA ocorrência. Pessoal comum: precisa ser frequente.
                limite = 0.3 if regra.categoria in {"pessoal", "empresa"} else 0
                if hits.mean() > limite and regra.categoria != "empresa" and (regra.tipo, regra.categoria) not in classes:
                    classes.append((regra.tipo, regra.categoria))
        for tipo, categoria in classes:
            preenchidos = int(df[coluna].notna().sum())
            exemplo = next((str(x) for x in df[coluna].dropna().head(1)), "")
            achados.append({
                "origem": nome_tabela,
                "pagina_ou_coluna": coluna,
                "tipo": tipo,
                "categoria": categoria,
                "valor_mascarado": mascarar(tipo, exemplo) if exemplo and categoria in {"pessoal", "financeiro"} else "[oculto]",
                "contexto": f"{preenchidos} registro(s) preenchido(s) nesta coluna",
                "_valor": "",
            })
    return achados


def anonimizar_tabela(df: pd.DataFrame, achados_tabela: list[dict]) -> pd.DataFrame:
    """Mascara colunas pessoais e remove colunas de dados sensíveis."""
    saida = df.copy()
    for achado in achados_tabela:
        coluna = achado["pagina_ou_coluna"]
        if coluna not in saida.columns:
            continue
        if achado["categoria"] in {"sensivel", "credencial", "crianca", "infraestrutura"}:
            saida = saida.drop(columns=[coluna])
        elif achado["tipo"] == "nome":
            saida[coluna] = saida[coluna].map(
                lambda x: None if pd.isna(x) else " ".join(p[0] + "." for p in str(x).split()))
        elif achado["tipo"] == "nascimento":
            # Mantém só o ano: preserva análises por faixa etária sem identificar a pessoa.
            saida[coluna] = saida[coluna].map(lambda x: (v.interpretar_data(x)[0] or x).year if hasattr(
                v.interpretar_data(x)[0] or x, "year") else None).astype("Int64")
        elif achado["tipo"] == "CEP":
            saida[coluna] = saida[coluna].map(lambda x: None if pd.isna(x) else str(x)[:5] + "-***")
        else:
            tipo = achado["tipo"]
            saida[coluna] = saida[coluna].map(lambda x, t=tipo: None if pd.isna(x) else mascarar(t, str(x)))
    return saida


def nivel_de_risco(achados: list[dict]) -> str:
    categorias = {a["categoria"] for a in achados}
    if categorias & {"credencial", "financeiro"}:
        return "CRÍTICO"
    if categorias & {"sensivel", "crianca", "infraestrutura"}:
        return "ALTO"
    if "pessoal" in categorias:
        return "MÉDIO"
    return "BAIXO"
