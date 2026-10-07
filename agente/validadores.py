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
    # Texto com palavras (URL, senha, código) não é número — só símbolos de moeda são aceitos.
    sem_moeda = re.sub(r"R\$|US\$|USD|BRL|EUR|reais|real|centavos?|€|\$", "", texto, flags=re.I)
    if re.search(r"[A-Za-zÀ-ú]{2,}|[@/:#]", sem_moeda):
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


# ---------- Normalizações para saneamento de cadastros ----------

MESES_ABREV = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
    "feb": 2, "apr": 4, "may": 5, "aug": 8, "sep": 9, "oct": 10, "dec": 12,
}
VAZIOS = {"", "nan", "none", "null", "n/a", "na", "nd", "-", "--", "s/n", "sem", "nat", "sem telefone", "sem email",
          "sem e-mail", "nao informado", "não informado", "nao tem", "não tem", "?"}


def vazio(valor) -> bool:
    if valor is None:
        return True
    try:
        if valor != valor:  # NaN
            return True
    except Exception:
        pass
    return str(valor).strip().lower() in VAZIOS


def corrigir_mojibake(texto: str) -> str:
    """Conserta texto UTF-8 lido como Latin-1 ('JoÃ£o' -> 'João'), inclusive duplo."""
    if not isinstance(texto, str):
        return texto
    for _ in range(2):
        if not re.search(r"[ÃÂ][\x80-\xbfŒ-™]|Ã[£§©ª¡³µº¢]", texto):
            break
        try:
            texto = texto.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                texto = texto.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
    return texto


def _ano(ano: str) -> int:
    n = int(ano)
    if len(ano) <= 2:
        limite = date.today().year % 100
        n += 2000 if n <= limite else 1900
    return n


def interpretar_data(valor, preferencia: str | dict = "DM") -> tuple[date | None, str]:
    """Converte datas em vários formatos. Retorna (data, alerta).

    `preferencia` decide datas ambíguas como 02/03/2024: "DM" (padrão brasileiro) ou "MD";
    pode ser um dict por separador ({"/": "MD", "-": "DM"}), vindo de `preferencia_de_datas`.
    """
    if vazio(valor):
        return None, ""
    texto = str(valor).strip().lower()
    if m := re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?$", texto):
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), ""
        except ValueError:
            return None, "data inexistente"
    if m := re.match(r"^(\d{1,2})[º°]?\s*(?:de\s+)?([a-zç]{3,9})\.?\s*(?:de\s+)?(\d{2,4})$", texto):
        mes = MESES_ABREV.get(remover_acentos(m[2])[:3])
        if mes:
            try:
                return date(_ano(m[3]), mes, int(m[1])), ""
            except ValueError:
                return None, "data inexistente"
    if m := re.match(r"^(\d{1,2})([/\-.])(\d{1,2})[/\-.](\d{2,4})$", texto):
        a, b, ano = int(m[1]), int(m[3]), _ano(m[4])
        if isinstance(preferencia, dict):
            preferencia = preferencia.get(m[2], "DM")
        if a > 12 and b <= 12:
            dia, mes, alerta = a, b, ""
        elif b > 12 and a <= 12:
            dia, mes, alerta = b, a, "formato americano MM/DD convertido"
        elif a == b:
            dia, mes, alerta = a, b, ""
        else:
            dia, mes = (a, b) if preferencia == "DM" else (b, a)
            alerta = f"data ambígua: assumido {'DD/MM' if preferencia == 'DM' else 'MM/DD'}"
        try:
            return date(ano, mes, dia), alerta
        except ValueError:
            return None, "data inexistente"
    convertida = converter_data(texto)
    return (convertida, "") if convertida else (None, "formato de data não reconhecido")


def preferencia_de_datas(valores) -> dict:
    """Olha a coluna inteira, separador por separador: se há mais datas claramente MM/DD
    que DD/MM (ex.: 07/14/2024), as ambíguas desse separador são lidas como MM/DD."""
    contagem: dict[str, list[int]] = {}
    for valor in valores:
        if m := re.match(r"^\s*(\d{1,2})([/\-.])(\d{1,2})[/\-.]\d{2,4}\s*$", str(valor or "")):
            a, b = int(m[1]), int(m[3])
            dm_md = contagem.setdefault(m[2], [0, 0])
            if a > 12 >= b:
                dm_md[0] += 1
            elif b > 12 >= a:
                dm_md[1] += 1
    return {sep: ("MD" if md > dm else "DM") for sep, (dm, md) in contagem.items()}


def normalizar_email(valor) -> tuple[str | None, str]:
    if vazio(valor):
        return None, "e-mail vazio"
    original = corrigir_mojibake(str(valor)).strip()
    email = re.sub(r"@+", "@", original.lower().replace(" ", ""))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        return None, f"e-mail inválido ({original})"
    alertas = []
    if "@@" in original:
        alertas.append("'@@' corrigido para '@'")
    elif original != original.lower():
        alertas.append("convertido para minúsculas")
    if not email.isascii():
        alertas.append(ALERTA_EMAIL_ACENTO)
    return email, "; ".join(alertas)


ALERTA_EMAIL_ACENTO = "e-mail com acento/caractere não-ASCII: válido pelo padrão (SMTPUTF8), mas muitos sistemas rejeitam"


PARTICULAS = {"da", "de", "do", "das", "dos", "e", "di", "du", "van", "von"}


def normalizar_nome(valor) -> str | None:
    if vazio(valor):
        return None
    texto = re.sub(r"\s+", " ", corrigir_mojibake(str(valor))).strip()
    palavras = []
    for i, p in enumerate(texto.lower().split(" ")):
        palavras.append(p if (i and p in PARTICULAS) else p[:1].upper() + p[1:])
    return " ".join(palavras)


def chave_nome(valor) -> str:
    return remover_acentos(normalizar_nome(valor) or "").lower()


CIDADES = {
    "poa": "Porto Alegre", "porto alegre": "Porto Alegre",
    "bh": "Belo Horizonte", "bhz": "Belo Horizonte", "belo horizonte": "Belo Horizonte",
    "rj": "Rio de Janeiro", "rio": "Rio de Janeiro", "rio de janeiro": "Rio de Janeiro",
    "sp": "São Paulo", "sampa": "São Paulo", "sao paulo": "São Paulo",
    "cwb": "Curitiba", "curitiba": "Curitiba", "bsb": "Brasília", "brasilia": "Brasília",
    "ssa": "Salvador", "salvador": "Salvador", "rec": "Recife", "recife": "Recife",
    "fortaleza": "Fortaleza", "manaus": "Manaus", "belem": "Belém", "goiania": "Goiânia",
    "floripa": "Florianópolis", "florianopolis": "Florianópolis", "vitoria": "Vitória",
    "joao pessoa": "João Pessoa", "teresina": "Teresina", "cuiaba": "Cuiabá", "maceio": "Maceió",
    "natal": "Natal", "aracaju": "Aracaju", "sao luis": "São Luís", "campo grande": "Campo Grande",
    "porto velho": "Porto Velho", "macapa": "Macapá", "boa vista": "Boa Vista", "palmas": "Palmas",
    "rio branco": "Rio Branco", "campinas": "Campinas", "niteroi": "Niterói", "santos": "Santos",
}


CIDADES_ABREV = {
    "bhorizonte": "Belo Horizonte", "bhte": "Belo Horizonte", "spaulo": "São Paulo", "sampa": "São Paulo",
    "rjaneiro": "Rio de Janeiro", "palegre": "Porto Alegre", "ptoalegre": "Porto Alegre", "fpolis": "Florianópolis",
    "cwb": "Curitiba", "rec": "Recife", "ssa": "Salvador", "poa": "Porto Alegre", "bsb": "Brasília",
    "for": "Fortaleza", "bel": "Belém", "mao": "Manaus", "gyn": "Goiânia", "nat": "Natal",
}


def normalizar_cidade(valor) -> tuple[str | None, str]:
    if vazio(valor):
        return None, ""
    texto = re.sub(r"\s+", " ", corrigir_mojibake(str(valor))).strip()
    # "recife-PE", "salvador/BA", "Curitiba - PR": tira a UF do fim.
    sem_uf = re.sub(r"\s*[-/,]\s*([A-Za-z]{2})$", lambda m: "" if m.group(1).upper() in UFS else m.group(0), texto)
    alerta_uf = "UF removida do nome da cidade" if sem_uf != texto else ""
    texto = sem_uf
    chave = remover_acentos(texto).lower()
    compacta = re.sub(r"[^a-z]", "", chave)
    if compacta in CIDADES_ABREV:
        return CIDADES_ABREV[compacta], "abreviação expandida"
    if chave in CIDADES:
        cidade = CIDADES[chave]
        return cidade, ("abreviação expandida" if len(chave) <= 4 and chave != cidade.lower() else alerta_uf)
    return normalizar_nome(texto), alerta_uf


def normalizar_telefone(valor, ddd_padrao: str | None = None, aceitar_sem_ddd: bool = False) -> tuple[str | None, str]:
    """Formato único: +55 (DD) 9XXXX-XXXX. Sem DDD, usa `ddd_padrao` (e avisa)."""
    if vazio(valor):
        return None, "telefone vazio"
    d = somente_digitos(valor)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if d.startswith("0") and len(d) in (11, 12):
        d = d[1:]
    alerta = ""
    if len(d) in (8, 9):
        if not ddd_padrao:
            if aceitar_sem_ddd:
                d = d if len(d) == 9 else d
                return (f"{d[:5]}-{d[5:]}" if len(d) == 9 else f"{d[:4]}-{d[4:]}"), "telefone SEM DDD na origem — completar"
            return None, f"telefone sem DDD ({valor})"
        d, alerta = ddd_padrao + d, f"DDD {ddd_padrao} inferido"
    if len(d) == 11 and d[2] == "9":
        return f"+55 ({d[:2]}) {d[2:7]}-{d[7:]}", alerta
    if len(d) == 10:
        return f"+55 ({d[:2]}) {d[2:6]}-{d[6:]}", alerta
    return None, f"telefone inválido ({valor})"


def normalizar_booleano(valor) -> bool | None:
    if vazio(valor):
        return None
    texto = remover_acentos(str(valor)).strip().lower()
    if texto in {"sim", "s", "yes", "y", "1", "true", "verdadeiro", "x", "pago", "ok", "1.0"}:
        return True
    if texto in {"nao", "n", "no", "0", "false", "falso", "0.0", "pendente"}:
        return False
    return None


def interpretar_valor(valor) -> tuple[float | None, str | None, str]:
    """('USD 1531.23') -> (1531.23, 'USD', ''); ('R$ 169,65') -> (169.65, 'BRL', '')."""
    if vazio(valor):
        return None, None, ""
    texto = str(valor).strip()
    moeda, alerta = None, ""
    simbolos = {"R$": "BRL", "BRL": "BRL", "US$": "USD", "USD": "USD", "$": "USD", "EUR": "EUR", "€": "EUR"}
    for simbolo, codigo in simbolos.items():
        if simbolo.lower() in texto.lower():
            moeda = codigo
            break
    if isinstance(valor, (int, float)) or re.fullmatch(r"-?\d+(\.\d+)?", texto):
        numero = float(valor)
    else:
        numero = converter_valor_monetario(texto)
    if numero is None:
        return None, None, f"valor não reconhecido ({texto})"
    if moeda is None:
        moeda, alerta = "BRL", "moeda não informada (assumido BRL)"
    elif moeda != "BRL":
        alerta = f"valor em {moeda} — não convertido para BRL"
    return round(numero, 2), moeda, alerta


def luhn_valido(numero: str) -> bool:
    d = somente_digitos(numero)
    if not 13 <= len(d) <= 19:
        return False
    soma = 0
    for i, c in enumerate(reversed(d)):
        n = int(c)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        soma += n
    return soma % 10 == 0


def formato_de(campo: str, valor) -> str:
    """Rótulo do FORMATO de um valor bruto — para relatar quantos formatos diferentes existem."""
    if vazio(valor):
        return "vazio/N/A"
    texto = str(valor)
    t = texto.strip()
    if campo == "cpf":
        if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", t):
            return "000.000.000-00"
        if re.fullmatch(r"\d{11}", t):
            return "só dígitos"
        if re.fullmatch(r"[\d ]+", t):
            return "dígitos com espaços"
        return "outro"
    if campo == "data":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", t):
            return "AAAA-MM-DD (ISO)"
        if re.fullmatch(r"\d{1,2}\s+de\s+\w+\.?\s+(de\s+)?\d{2,4}", t, re.IGNORECASE):
            return "DD de mês AAAA"
        if m := re.fullmatch(r"(\d{1,2})([/\-.])(\d{1,2})[/\-.](\d{2,4})", t):
            sep, ano = m[2], "AAAA" if len(m[4]) == 4 else "AA"
            a, b = int(m[1]), int(m[3])
            if b > 12 >= a:
                return f"MM{sep}DD{sep}{ano} (americano)"
            if a <= 12 and b <= 12 and a != b:
                return f"DD{sep}MM{sep}{ano} ambígua"
            return f"DD{sep}MM{sep}{ano}"
        return "outro"
    if campo == "telefone":
        if t.startswith("+55"):
            return "+55DDNÚMERO"
        if re.fullmatch(r"\(\d{2}\)\s?\d{4,5}-\d{4}", t):
            return "(DD) NNNNN-NNNN"
        d = somente_digitos(t)
        if len(d) in (10, 11):
            return "DDNÚMERO só dígitos"
        if len(d) in (8, 9):
            return "sem DDD"
        return "outro"
    if campo == "email":
        if "@@" in t:
            return "com @@"
        if t != t.lower():
            return "MAIÚSCULAS"
        return "minúsculas" if re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", t) else "inválido"
    if campo == "nome":
        if texto != texto.strip():
            return "espaços sobrando"
        if t.isupper():
            return "MAIÚSCULAS"
        if t.islower():
            return "minúsculas"
        return "Normal"
    return "—"


# ---------- Data e hora com fuso; preços ----------
from datetime import datetime, timedelta, timezone  # noqa: E402

FUSO_BRASILIA = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019


def interpretar_data_hora(valor, preferencia: str | dict = "DM", utc: bool = False,
                          ano_padrao: int | None = None) -> tuple[datetime | None, bool, str]:
    """Data/hora em horário de Brasília. Retorna (datetime sem fuso, tem_hora, alerta).

    Entende: ISO com 'Z'/offset (converte para Brasília), coluna marcada como UTC, epoch em
    segundos/milissegundos (UTC), número serial do Excel, 'dd/mm' sem ano (usa `ano_padrao`)
    e todos os formatos de `interpretar_data`.
    """
    if vazio(valor):
        return None, False, ""
    texto = str(valor).strip()
    if re.fullmatch(r"\d+(\.\d+)?", texto):
        n = float(texto)
        if 20000 <= n <= 80000:
            data = datetime(1899, 12, 30) + timedelta(days=n)
            return data, n % 1 != 0, "data em número serial do Excel convertida"
        if 9e8 <= n <= 4.2e9 or 9e11 <= n <= 4.2e12:
            segundos = n / 1000 if n > 1e11 else n
            em_utc = datetime.fromtimestamp(segundos, timezone.utc)
            data = em_utc.astimezone(FUSO_BRASILIA).replace(tzinfo=None)
            alerta = "data em epoch (segundos desde 1970, UTC) convertida para horário de Brasília"
            if data.date() != em_utc.date():
                alerta += (f" — ATENÇÃO: o dia muda com o fuso (em UTC seria {em_utc:%d/%m/%Y %H:%M}); "
                           "se o sistema de origem gravou horário local, a data correta é a de UTC")
            return data, True, alerta
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s*(Z|[+-]\d{2}:?\d{2}|UTC)?", texto, re.I)
    if m:
        try:
            data = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
        except ValueError:
            return None, False, "data inexistente"
        fuso = (m.group(3) or "").upper()
        if fuso in {"Z", "UTC"} or (not fuso and utc):
            data = data.replace(tzinfo=timezone.utc).astimezone(FUSO_BRASILIA).replace(tzinfo=None)
            return data, True, "horário UTC convertido para Brasília (−3h)"
        if fuso:
            offset = fuso.replace(":", "")
            tz = timezone(timedelta(hours=int(offset[:3]), minutes=int(offset[0] + offset[3:])))
            data = data.replace(tzinfo=tz).astimezone(FUSO_BRASILIA).replace(tzinfo=None)
        return data, True, ""
    if (m := re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})", texto)) and ano_padrao:
        a, b = int(m[1]), int(m[2])
        dia, mes = (a, b) if (preferencia if isinstance(preferencia, str) else preferencia.get("/", "DM")) == "DM" or b > 12 else (b, a)
        try:
            return datetime(ano_padrao, mes, dia), False, f"data sem ano: assumido {ano_padrao} (ano do arquivo)"
        except ValueError:
            return None, False, "data inexistente"
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(\d{1,2}:\d{2})(?::\d{2})?(?:\s*\((BRT|UTC)\))?", texto, re.I):
        data, alerta = interpretar_data(f"{m[1]}/{m[2]}/{m[3]}", preferencia)
        if data is None:
            return None, False, alerta or "data inválida"
        h, mi = map(int, m[4].split(":"))
        dt = datetime(data.year, data.month, data.day, h, mi)
        if (m[5] or "").upper() == "UTC" or (not m[5] and utc):
            dt = dt.replace(tzinfo=timezone.utc).astimezone(FUSO_BRASILIA).replace(tzinfo=None)
            alerta = (alerta + "; " if alerta else "") + "horário UTC convertido para Brasília (−3h)"
        return dt, True, alerta
    data, alerta = interpretar_data(texto, preferencia)
    if data is None:
        return None, False, alerta or "data inválida"
    return datetime(data.year, data.month, data.day), False, alerta


def interpretar_preco(valor) -> tuple[float | None, str]:
    """'R$ 12,34' | '12.34' | '1234 centavos' → 12.34."""
    if vazio(valor):
        return None, ""
    texto = str(valor).strip().lower()
    if re.search(r"centavos?|cents?\b", texto):
        d = re.sub(r"[^\d]", "", texto)
        return (int(d) / 100 if d else None), "preço em centavos convertido para reais"
    numero = converter_valor_monetario(texto)
    return numero, ("" if numero is not None else f"preço não reconhecido ({valor})")


def converter_peso_kg(valor, unidade) -> tuple[float | None, str]:
    numero = converter_valor_monetario(valor) if not isinstance(valor, (int, float)) else float(valor)
    if numero is None:
        return None, ""
    u = remover_acentos(str(unidade or "kg")).strip().lower()
    if u in {"g", "gr", "grama", "gramas"}:
        return round(numero / 1000, 4), "peso em gramas convertido para kg"
    if u in {"mg"}:
        return round(numero / 1e6, 6), "peso em mg convertido para kg"
    if u in {"t", "ton", "tonelada"}:
        return round(numero * 1000, 3), "peso em toneladas convertido para kg"
    return round(numero, 4), ""
