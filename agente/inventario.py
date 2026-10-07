"""Inventário de uma pasta/ZIP enviada: o que é lixo, duplicado, mal nomeado ou secreto.

Antes de ler qualquer dado, o agente olha a ESTRUTURA:
- arquivos de sistema (Thumbs.db, desktop.ini, .DS_Store, ~$lock do Office) e vazios → removidos;
- arquivos idênticos (mesmo conteúdo, SHA-256) → fica um, os outros são marcados como duplicata;
- nomes ruins ("Nova pasta (2)", "sem título", "final final", "v2_AGORA_VAI", "(cópia)") → nome sugerido;
- arquivos de credenciais (.env, senhas*.txt, *.pem, id_rsa) → quarentena (só análise, nunca copiados).
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .validadores import remover_acentos

LIXO_NOMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "icon\r", ".localized"}
LIXO_PADROES = (r"^~\$", r"^\._", r"^~.*\.tmp$", r"\.tmp$", r"^\.~lock\.", r"\.crdownload$", r"\.part$")
LIXO_PASTAS = ("__macosx/", ".git/", ".svn/", "$recycle.bin/")
SEGREDO_NOMES = re.compile(
    r"(^\.env(\..*)?$|^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|pfx|p12|jks|keystore|kdbx)$|"
    r"senha|password|passwd|credencia|credential|secret|segredo|token)",
    re.IGNORECASE,
)
NOMES_RUINS = (
    (r"nova pasta|new folder", "nome padrão do sistema ('Nova pasta')"),
    (r"sem t[ií]tulo|untitled|documento\d*$", "arquivo sem título"),
    (r"\bfinal\b.*\bfinal\b|final[_ ]?final", "'final final'"),
    (r"(?<![a-z])v\d+(?![a-z0-9])|vers[aã]o ?\d", "versão no nome (v2...)"),
    (r"agora[_ ]?vai|definitiv|ultim[oa]|\bok\b|revisad", "nome informal (AGORA_VAI, definitivo...)"),
    (r"\bfinal\b", "'final' no nome"),
    (r"c[óo]pia|copy|\(\d+\)", "cópia / numeração automática"),
    (r"\bold\b|_old|antig|backup|bkp", "pasta/arquivo de backup antigo"),
    (r"\s", "espaços no nome"),
    (r"(?=.*[a-z])[A-Z]{3,}", "maiúsculas misturadas"),
    (r"[^\x00-\x7f]", "acentos/caracteres especiais"),
)
RUIDO_NOME = re.compile(
    r"final|v\d+|agora[_ ]?vai|definitiv\w*|c[óo]pia|copy|\(\d+\)|nova pasta|new folder|"
    r"sem t[ií]tulo|untitled|old|antig\w*|backup|bkp|ultim[oa]|revisad\w*",
    re.IGNORECASE,
)
EXT_DADOS = {".csv", ".xlsx", ".xlsm", ".xls", ".json", ".tsv", ".jsonl", ".ndjson", ".xml", ".db", ".sqlite",
             ".sqlite3", ".sql"}
EXT_DOCUMENTO = {".pdf", ".docx", ".html", ".htm", ".md", ".rtf", ".txt", ".log", ".eml"}
EXT_CONFIG = {".yaml", ".yml", ".ini", ".conf", ".cfg", ".toml", ".properties", ".env", ".bak", ".config"}
SEGREDO_CONTEUDO = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:password|passwd|senha|secret(?:_key)?|api[_-]?key|token|aws_secret)\b\s*[:=]\s*\S+"
    r"|\b[a-z][a-z0-9+.-]*://[^:\s/]+:[^@\s]+@|\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)
LIMITE_DESCOMPACTADO = 500 * 1024 * 1024
LIMITE_ARQUIVOS = 5000


@dataclass
class Item:
    caminho: str
    conteudo: bytes
    sha256: str = ""
    categoria: str = ""  # dados | documento | segredo | lixo | vazio | duplicata | nao_suportado
    situacao: str = ""
    problemas_nome: list[str] = field(default_factory=list)
    nome_sugerido: str = ""
    observacoes: list[str] = field(default_factory=list)

    @property
    def nome(self) -> str:
        return PurePosixPath(self.caminho).name

    @property
    def extensao(self) -> str:
        return PurePosixPath(self.caminho).suffix.lower()


def expandir(arquivos: list[tuple[str, bytes]]) -> list[Item]:
    """Abre ZIPs (inclusive dentro de ZIPs) e devolve a lista plana de arquivos com caminho."""
    itens: list[Item] = []
    total = 0

    def adicionar(caminho: str, conteudo: bytes, profundidade: int = 0):
        nonlocal total
        if caminho.lower().endswith(".zip") and profundidade < 3:
            with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
                # ZIP dentro do ZIP: os arquivos internos ficam em "<caminho do zip>/<nome>".
                raiz = caminho if profundidade else ""
                if profundidade:
                    conteiner = Item(caminho, conteudo, categoria="conteiner")
                    conteiner.situacao = f"ZIP aninhado aberto ({sum(not i.is_dir() for i in z.infolist())} arquivo(s) dentro)"
                    itens.append(conteiner)
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    total += info.file_size
                    if total > LIMITE_DESCOMPACTADO or len(itens) > LIMITE_ARQUIVOS:
                        raise ValueError("ZIP grande demais (limite de 500 MB / 5000 arquivos descompactados).")
                    nome = _nome_zip(info)
                    adicionar(f"{raiz}/{nome}" if raiz else nome, z.read(info), profundidade + 1)
            return
        itens.append(Item(caminho.replace("\\", "/"), conteudo))

    for nome, conteudo in arquivos:
        adicionar(nome, conteudo)
    # Se tudo está dentro de uma única pasta raiz (ZIP de uma pasta), tira esse prefixo.
    raizes = {i.caminho.split("/", 1)[0] for i in itens}
    if len(raizes) == 1 and all("/" in i.caminho for i in itens):
        prefixo = raizes.pop() + "/"
        for i in itens:
            i.caminho = i.caminho[len(prefixo):]
    return itens


def _nome_zip(info: zipfile.ZipInfo) -> str:
    nome = info.filename
    if not info.flag_bits & 0x800:  # sem flag UTF-8: nomes do Windows vêm em CP437
        try:
            nome = nome.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return nome


def _limpar_nome(caminho: str) -> str:
    p = PurePosixPath(caminho)
    base = remover_acentos(p.stem)
    base = RUIDO_NOME.sub(" ", base)
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower()
    base = re.sub(r"_+", "_", base)
    if base and re.search(r"c[óo]pia|copy", p.name, re.IGNORECASE):
        base += "_copia"
    if base and re.search(r"backup|bkp|\bold\b|_old|antig", caminho, re.IGNORECASE):
        base += "_antigo"
    if not base:
        # Nome era só ruído ("Nova pasta (2)/arquivo.xlsx" já cai no caso normal; aqui sobra a pasta).
        pasta = [x for x in p.parent.parts if not RUIDO_NOME.fullmatch(x.strip())]
        base = re.sub(r"[^a-z0-9]+", "_", remover_acentos(pasta[-1]).lower()).strip("_") if pasta else "arquivo"
    return base + p.suffix.lower()


def analisar(itens: list[Item]) -> list[Item]:
    vistos: dict[str, Item] = {}
    # Fica com a versão "melhor" de cada conteúdo: fora de backup e mais perto da raiz.
    ordem = sorted(itens, key=lambda i: (bool(re.search(r"backup|old|antig|c[óo]pia|copy|lixeira|trash|\.bak$|nao usar|não usar",
                                                         i.caminho, re.I)),
                                         i.caminho.count("/"), i.caminho))
    for item in ordem:
        item.sha256 = hashlib.sha256(item.conteudo).hexdigest()
        nome = item.nome.lower()
        caminho = item.caminho.lower()

        for padrao, descricao in NOMES_RUINS:
            alvo = PurePosixPath(item.caminho).stem if "[A-Z]" in padrao else caminho
            if descricao == "'final' no nome" and "'final final'" in item.problemas_nome:
                continue
            if re.search(padrao, alvo):
                item.problemas_nome.append(descricao)

        if nome in LIXO_NOMES or any(re.search(p, nome) for p in LIXO_PADROES) or any(p in caminho for p in LIXO_PASTAS):
            item.categoria, item.situacao = "lixo", "REMOVER — arquivo de sistema/temporário"
            if nome.startswith("~$"):
                item.situacao = "REMOVER — arquivo de trava do Office (~$), criado ao abrir a planilha"
            continue
        if not item.conteudo.strip():
            item.categoria, item.situacao = "vazio", "REMOVER — arquivo vazio"
            continue
        if item.sha256 in vistos:
            original = vistos[item.sha256]
            item.categoria = "duplicata"
            item.situacao = f"REMOVER — cópia idêntica de '{original.caminho}'"
            continue
        vistos[item.sha256] = item
        if item.categoria == "conteiner":
            continue
        texto_inicio = item.conteudo[:200_000].decode("utf-8", "ignore")
        eh_config = item.extensao in EXT_CONFIG or (not item.extensao and item.conteudo[:16] != b"SQLite format 3\x00")
        if SEGREDO_NOMES.search(nome) or (eh_config and SEGREDO_CONTEUDO.search(texto_inicio)):
            item.categoria = "segredo"
            item.situacao = "QUARENTENA — credenciais/segredos: analisado, NÃO copiado para a saída"
        elif item.extensao in EXT_DADOS or item.conteudo[:16] == b"SQLite format 3\x00":
            item.categoria, item.situacao = "dados", "processado"
        elif item.extensao in EXT_DOCUMENTO:
            item.categoria, item.situacao = "documento", "processado"
        else:
            item.categoria, item.situacao = "nao_suportado", "não processado (formato não suportado)"
        if item.categoria in {"dados", "documento"}:
            item.nome_sugerido = _limpar_nome(item.caminho)

    # Nomes sugeridos únicos por pasta de destino.
    usados: dict[str, int] = {}
    for item in itens:
        if item.nome_sugerido:
            n = usados.get(item.nome_sugerido, 0)
            usados[item.nome_sugerido] = n + 1
            if n:
                p = PurePosixPath(item.nome_sugerido)
                item.nome_sugerido = f"{p.stem}_{n + 1}{p.suffix}"
    return itens


def tabela(itens: list[Item]):
    import pandas as pd

    return pd.DataFrame([
        {
            "caminho": i.caminho,
            "tamanho_bytes": len(i.conteudo),
            "categoria": i.categoria,
            "situacao": i.situacao,
            "problemas_no_nome": "; ".join(dict.fromkeys(i.problemas_nome)),
            "nome_sugerido": i.nome_sugerido,
            "observacoes": "; ".join(i.observacoes),
            "sha256": i.sha256[:16],
        }
        for i in sorted(itens, key=lambda i: i.caminho)
    ])
