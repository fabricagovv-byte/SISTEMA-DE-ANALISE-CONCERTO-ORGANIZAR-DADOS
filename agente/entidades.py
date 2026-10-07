"""Base única de pessoas (clientes, beneficiários...) e vínculo das transações ao seu ID.

1. Reconhece o papel de cada coluna pelo nome (id, nome, cpf, email, telefone, cliente...),
   mesmo com esquemas diferentes ("documento" = cpf, "id_cliente" = id, "PedidoID" = id).
2. Separa tabelas de PESSOAS (cadastros) de tabelas de TRANSAÇÕES (vendas, pedidos...).
3. Junta registros da mesma pessoa vindos de qualquer arquivo (mesmo CPF, e-mail, nome ou ID)
   e monta um registro "de ouro" escolhendo, campo a campo, o melhor valor válido da fonte
   mais confiável — fontes de backup/antigas valem menos. Tudo que diverge vira conflito.
4. Liga cada transação ao ID da pessoa: por ID, e-mail, CPF, nome completo ou primeiro nome.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import validadores as v
from .normalizador import padronizar_nome_coluna

PAPEIS = {
    "id": {"id", "codigo", "cod", "id_cliente", "cliente_id", "cod_cliente", "pedido_id", "pedidoid",
           "id_pedido", "id_venda", "venda_id", "vendaid", "numero", "num", "matricula", "id_beneficiario",
           "pedido", "order", "order_ref", "order_id", "customer_id"},
    "nome": {"nome", "nome_completo", "name", "full_name", "nome_cliente", "nm_cliente", "razao_social", "nome_beneficiario"},
    "cpf": {"cpf", "documento", "doc", "nr_cpf", "cpf_cnpj", "num_documento", "cpf_cliente", "tax_id"},
    "email": {"email", "e_mail", "mail", "correio_eletronico", "email_cliente"},
    "telefone": {"telefone", "fone", "tel", "celular", "whatsapp", "phone", "telefone_celular"},
    "cidade": {"cidade", "municipio", "city", "localidade", "cidade_uf", "municipio_uf", "cidade_estado"},
    "nascimento": {"dt_nasc", "data_nascimento", "nascimento", "data_nasc", "dt_nascimento", "birth", "birthdate"},
    "senha": {"senha", "password", "pass", "pwd", "senha_acesso", "passwd", "senha_md5", "senha_hash", "password_hash"},
    "cartao": {"cartao", "card", "numero_cartao", "cartao_credito", "cc", "num_cartao"},
    "cliente_ref": {"cliente", "cli", "cliente_id", "id_cliente", "cod_cliente", "comprador", "beneficiario",
                    "cliente_nome", "cliente_cpf", "cliente_email", "cliente_documento", "customer"},
    "valor": {"valor", "vlr", "preco", "total", "amount", "valor_total", "vl_pago", "valor_pago", "amount_value"},
    "data": {"data", "dt", "date", "data_venda", "data_pedido", "dt_venda", "dt_pagamento", "data_pagamento",
             "created_at", "created_at_utc", "data_hora", "dia", "ts", "timestamp"},
    "sku": {"sku", "cod_produto", "codigo_produto", "produto_sku"},
    "qtd": {"qtd", "qty", "quantidade", "quant", "qtde"},
    "pago": {"pago", "status_pagamento", "paid", "quitado", "foi_pago"},
    "status": {"status", "situacao", "estado_pedido"},
    "itens": {"itens", "items", "produtos", "produto", "item"},
}
PAPEIS_PESSOA = ("nome", "cpf", "email", "telefone", "nascimento")
PAPEIS_TRANSACAO = ("valor", "itens", "sku", "qtd", "pago")
TIPOS_DE_PESSOA = (
    ("leads", re.compile(r"lead|prospect|feira|evento|campanha|newsletter|interessad", re.I)),
    ("colaboradores", re.compile(r"folha|(^|/)rh(/|_|$)|funcion|colaborad|servidor|vendedor|comiss|holerite|salari", re.I)),
)
INATIVO = re.compile(r"exclu|delet|inativ|cancelad|bloquead|removid|desativ", re.I)
RUIM_NO_CAMINHO = re.compile(r"backup|bkp|\bold\b|_old|antig|obsolet|lixeira|arquivo morto", re.IGNORECASE)
COPIA_NO_CAMINHO = re.compile(r"c[óo]pia|copy|\(\d+\)", re.IGNORECASE)


def papel(coluna: str) -> str | None:
    base = re.sub(r"_\d+$", "", coluna)
    for nome, sinonimos in PAPEIS.items():
        if nome == "cliente_ref":
            continue
        if base in sinonimos:
            return nome
    if base in PAPEIS["cliente_ref"] or base.startswith(("cliente_", "customer_")):
        return "cliente_ref"
    return None


@dataclass
class Fonte:
    tabela: str
    caminho: str
    tipo: str  # "pessoas" | "transacoes" | "outra"
    colunas: dict[str, list[str]]
    prioridade: int = 0
    motivo: str = ""
    tipo_pessoa: str = ""  # clientes | leads | colaboradores


@dataclass
class ResultadoEntidades:
    pessoas: pd.DataFrame
    conflitos: pd.DataFrame
    transacoes: dict[str, pd.DataFrame]
    correcoes: pd.DataFrame
    fontes: pd.DataFrame
    nao_vinculados: pd.DataFrame
    resumo: dict = field(default_factory=dict)
    outros_cadastros: dict = field(default_factory=dict)
    resolver: object = None  # função(valor) -> (id_cliente | None, método)


def _colunas_padronizadas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    nomes, vistos = [], {}
    for c in df.columns:
        n = padronizar_nome_coluna(c)
        vistos[n] = vistos.get(n, 0) + 1
        nomes.append(n if vistos[n] == 1 else f"{n}_{vistos[n]}")
    df.columns = nomes
    return df


def _analisar_fonte(nome: str, df: pd.DataFrame, caminho: str) -> Fonte:
    colunas: dict[str, list[str]] = {}
    for c in df.columns:
        if (p := papel(c)) is not None:
            colunas.setdefault(p, []).append(c)
    pessoais = sum(1 for p in PAPEIS_PESSOA if p in colunas)
    refs_cliente = [c for c in colunas.get("cliente_ref", []) if re.sub(r"_\d+$", "", c) not in PAPEIS["id"]]
    transacional = any(p in colunas for p in PAPEIS_TRANSACAO)
    tem_id = bool(colunas.get("id")) or bool(colunas.get("cliente_ref"))
    # Cadastro completo (2+ atributos) ou atualização parcial (ID + 1 atributo, ex.: novo e-mail).
    if not transacional and not refs_cliente and (pessoais >= 2 or (pessoais >= 1 and tem_id)):
        tipo = "pessoas"
        # Em cadastro, "id_cliente" é o próprio ID.
        for c in colunas.pop("cliente_ref", []):
            colunas.setdefault("id", []).append(c)
    elif "cliente_ref" in colunas:
        tipo = "transacoes"
        for c in list(colunas.get("id", [])):
            if re.sub(r"_\d+$", "", c) in {"id_cliente", "cliente_id", "cod_cliente"}:
                colunas["id"].remove(c)
                colunas["cliente_ref"].append(c)
    else:
        tipo = "outra"
    fonte = Fonte(nome, caminho, tipo, colunas)
    if tipo == "pessoas":
        alvo = f"{caminho} {nome}"
        fonte.tipo_pessoa = next((t for t, padrao in TIPOS_DE_PESSOA if padrao.search(alvo)), "clientes")
        prioridade, motivos = 100, []
        if RUIM_NO_CAMINHO.search(caminho):
            prioridade -= 40
            motivos.append("pasta/arquivo de backup ou antigo")
        if COPIA_NO_CAMINHO.search(caminho):
            prioridade -= 20
            motivos.append("arquivo marcado como cópia")
        anos = [int(a) for a in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", caminho)]
        if anos and max(anos) < date.today().year - 1:
            prioridade -= 25
            motivos.append(f"dados de {max(anos)} (desatualizados)")
        prioridade += 3 * pessoais + min(len(df), 1000) // 100
        fonte.prioridade = prioridade
        fonte.motivo = "; ".join(motivos) or "fonte principal"
    return fonte


class _Uniao:
    def __init__(self, n):
        self.pai = list(range(n))

    def achar(self, x):
        while self.pai[x] != x:
            self.pai[x] = self.pai[self.pai[x]]
            x = self.pai[x]
        return x

    def unir(self, a, b):
        self.pai[self.achar(a)] = self.achar(b)


def _id_texto(valor) -> str | None:
    if v.vazio(valor):
        return None
    texto = str(valor).strip()
    if re.fullmatch(r"\d+(\.0+)?", texto):
        return str(int(float(texto)))
    return texto


def _cpf_digitos(valor) -> str | None:
    if v.vazio(valor):
        return None
    d = v.somente_digitos(valor)
    return d.zfill(11) if 9 <= len(d) <= 11 else None


def _primeiro(fonte: Fonte, papel_: str, linha: pd.Series):
    for c in fonte.colunas.get(papel_, []):
        if not v.vazio(linha.get(c)):
            return linha[c]
    return None


def _ddd_dominante(valores) -> str | None:
    ddds = []
    for x in valores:
        d = v.somente_digitos(x or "")
        if d.startswith("55") and len(d) in (12, 13):
            d = d[2:]
        if len(d) in (10, 11):
            ddds.append(d[:2])
    if not ddds:
        return None
    mais = max(set(ddds), key=ddds.count)
    return mais if ddds.count(mais) / len(ddds) >= 0.8 else None


def consolidar(
    brutas: dict[str, pd.DataFrame],
    caminhos: dict[str, str] | None = None,
    textos: list[tuple[str, str]] | None = None,
) -> ResultadoEntidades | None:
    """`textos`: (origem, texto) de documentos/logs, usados para recuperar CPF de quem está inválido."""
    caminhos = caminhos or {}
    tabelas = {n: _colunas_padronizadas(df) for n, df in brutas.items()}
    fontes = {n: _analisar_fonte(n, df, caminhos.get(n, n)) for n, df in tabelas.items()}
    todas_pessoas = [f for f in fontes.values() if f.tipo == "pessoas"]
    if not todas_pessoas:
        return None
    # Base única só de CLIENTES; leads e colaboradores são cadastros de outra natureza.
    tipos_presentes = {f.tipo_pessoa for f in todas_pessoas}
    principal = "clientes" if "clientes" in tipos_presentes else max(
        tipos_presentes, key=lambda t: sum(len(tabelas[f.tabela]) for f in todas_pessoas if f.tipo_pessoa == t))
    de_pessoas = [f for f in todas_pessoas if f.tipo_pessoa == principal]
    outros_cadastros = {
        f.tabela: _cadastro_simples(f, tabelas[f.tabela]) for f in todas_pessoas if f.tipo_pessoa != principal
    }
    correcoes: list[dict] = []

    def corrigir(tabela, linha, campo, original, novo, alerta):
        if alerta or (str(original or "").strip() != str(novo or "").strip()):
            correcoes.append({
                "tabela": tabela, "linha": linha, "campo": campo,
                "valor_original": original, "valor_corrigido": novo, "alerta": alerta,
            })

    # ---- 1. Registros de pessoas normalizados ----
    todos_telefones = [x for f in de_pessoas for c in f.colunas.get("telefone", []) for x in tabelas[f.tabela][c]]
    ddd = _ddd_dominante(todos_telefones)
    registros: list[dict] = []
    for f in sorted(de_pessoas, key=lambda f: -f.prioridade):
        df = tabelas[f.tabela]
        preferencias = {c: v.preferencia_de_datas(df[c]) for c in f.colunas.get("nascimento", [])}
        for i, linha in df.iterrows():
            numero_linha = i + 2
            r = {"fonte": f.tabela, "prioridade": f.prioridade, "linha": numero_linha, "senha_exposta": False,
                 "desatualizada": f.motivo != "fonte principal" and ("antigo" in f.motivo or "desatualiz" in f.motivo)}
            r["id"] = _id_texto(_primeiro(f, "id", linha))
            r["id_variante"] = None
            if r["id"] and (m := re.fullmatch(r"(\d+)\s*[-_/.]?\s*([A-Za-z]{1,2})", r["id"])):
                r["id_variante"], r["id"] = r["id"], m.group(1)
                corrigir(f.tabela, numero_linha, "id", r["id_variante"], r["id"], "código com sufixo (provável cadastro duplicado)")
            status_bruto = _primeiro(f, "status", linha)
            r["inativo"] = bool(status_bruto and INATIVO.search(str(status_bruto)))
            r["status_original"] = status_bruto
            nome_bruto = _primeiro(f, "nome", linha)
            r["nome"] = v.normalizar_nome(nome_bruto)
            corrigir(f.tabela, numero_linha, "nome", nome_bruto, r["nome"], "")
            cpf_bruto = _primeiro(f, "cpf", linha)
            r["cpf"] = _cpf_digitos(cpf_bruto)
            r["cpf_valido"] = bool(r["cpf"] and v.cpf_valido(r["cpf"]))
            if cpf_bruto is not None:
                corrigir(f.tabela, numero_linha, "cpf", cpf_bruto,
                         v.formatar_cpf(r["cpf"]) if r["cpf_valido"] else cpf_bruto,
                         "" if r["cpf_valido"] else "CPF com dígito verificador inválido")
            email_bruto = _primeiro(f, "email", linha)
            r["email"], alerta = v.normalizar_email(email_bruto) if f.colunas.get("email") else (None, "")
            if f.colunas.get("email"):
                corrigir(f.tabela, numero_linha, "email", email_bruto, r["email"], alerta)
            tel_bruto = _primeiro(f, "telefone", linha)
            if f.colunas.get("telefone"):
                r["telefone"], alerta = v.normalizar_telefone(tel_bruto, ddd, aceitar_sem_ddd=True)
                corrigir(f.tabela, numero_linha, "telefone", tel_bruto, r["telefone"], alerta)
            else:
                r["telefone"] = None
            cid_bruto = _primeiro(f, "cidade", linha)
            r["cidade"], alerta = v.normalizar_cidade(cid_bruto)
            if cid_bruto is not None:
                corrigir(f.tabela, numero_linha, "cidade", cid_bruto, r["cidade"], alerta)
            r["nascimento"] = None
            for c in f.colunas.get("nascimento", []):
                data, alerta = v.interpretar_data(linha.get(c), preferencias[c])
                r["nascimento"] = data
                corrigir(f.tabela, numero_linha, "data_nascimento", linha.get(c),
                         data.isoformat() if data else None, alerta or ("" if data or v.vazio(linha.get(c)) else "data inválida"))
                break
            if any(not v.vazio(linha.get(c)) for c in f.colunas.get("senha", [])):
                r["senha_exposta"] = True
            r["qualidade"] = (2 * r["cpf_valido"] + (r["email"] is not None) + (r.get("telefone") is not None)
                              + (r["nascimento"] is not None) + (r["cidade"] is not None))
            registros.append(r)

    # Diagnóstico: quantos formatos diferentes cada campo tinha em cada fonte.
    formatos: dict[str, dict[str, dict[str, int]]] = {}
    for f in de_pessoas:
        df = tabelas[f.tabela]
        for papel_, campo in (("cpf", "cpf"), ("nascimento", "data"), ("telefone", "telefone"), ("email", "email"), ("nome", "nome")):
            for c in f.colunas.get(papel_, [])[:1]:
                contagem = df[c].map(lambda x, campo=campo: v.formato_de(campo, x)).value_counts().to_dict()
                formatos.setdefault(f.tabela, {})[papel_] = {k: int(n) for k, n in contagem.items()}
    for f in fontes.values():
        if f.tipo == "transacoes":
            df = tabelas[f.tabela]
            for c in f.colunas.get("data", [])[:1]:
                contagem = df[c].map(lambda x: v.formato_de("data", x)).value_counts().to_dict()
                formatos.setdefault(f.tabela, {})["data"] = {k: int(n) for k, n in contagem.items()}
            for c in f.colunas.get("valor", [])[:1]:
                def tipo_valor(x):
                    if v.vazio(x):
                        return "vazio ou '-'"
                    t = str(x)
                    return "USD" if "usd" in t.lower() else "R$ texto" if "r$" in t.lower() else "número"
                formatos.setdefault(f.tabela, {})["valor"] = {k: int(n) for k, n in df[c].map(tipo_valor).value_counts().to_dict().items()}
            for c in f.colunas.get("pago", [])[:1]:
                formatos.setdefault(f.tabela, {})["pago"] = {
                    str(k): int(n) for k, n in df[c].map(lambda x: "vazio" if v.vazio(x) else str(x).strip()).value_counts().to_dict().items()}
            def tipo_ref(linha):
                for c in f.colunas.get("cliente_ref", []):
                    x = linha.get(c)
                    if v.vazio(x):
                        continue
                    t = str(x).strip()
                    if "_" in c and c.split("_")[0] in {"cliente", "comprador"}:
                        return "objeto aninhado (nome + CPF)"
                    if "@" in t:
                        return "e-mail"
                    if t.isdigit():
                        return "ID"
                    return "nome completo" if len(t.split()) > 1 else "só primeiro nome"
                return "vazio"
            if f.colunas.get("cliente_ref"):
                formatos.setdefault(f.tabela, {})["cliente"] = {
                    k: int(n) for k, n in df.apply(tipo_ref, axis=1).value_counts().to_dict().items()}
            if f.colunas.get("status"):
                brutos = df.apply(lambda linha: _primeiro(f, "status", linha), axis=1)
                formatos.setdefault(f.tabela, {})["status"] = {
                    str(k): int(n) for k, n in brutos.map(lambda x: "vazio" if v.vazio(x) else str(x)).value_counts().to_dict().items()}

    # ---- 2. Agrupar registros da mesma pessoa ----
    # Restrições: dois IDs diferentes da MESMA fonte nunca são a mesma pessoa (homônimos,
    # CPF digitado errado); ligação só por nome também não junta quem tem CPFs válidos diferentes.
    uniao = _Uniao(len(registros))
    ids_por_fonte = [{r["fonte"]: {r["id"]}} if r["id"] else {} for r in registros]
    cpfs_validos = [{r["cpf"]} if r["cpf_valido"] else set() for r in registros]
    bloqueios: list[dict] = []

    def pode_unir(a, b, por_nome=False):
        ra, rb = uniao.achar(a), uniao.achar(b)
        if ra == rb:
            return True
        for fonte_, ids_a in ids_por_fonte[ra].items():
            ids_b = ids_por_fonte[rb].get(fonte_)
            if ids_b and ids_a != ids_b:
                return False
        if por_nome and cpfs_validos[ra] and cpfs_validos[rb] and not (cpfs_validos[ra] & cpfs_validos[rb]):
            return False
        return True

    def unir(a, b, motivo, por_nome=False):
        ra, rb = uniao.achar(a), uniao.achar(b)
        if ra == rb:
            return
        if not pode_unir(a, b, por_nome):
            bloqueios.append({"a": a, "b": b, "motivo": motivo})
            return
        uniao.unir(a, b)
        novo = uniao.achar(a)
        juntos: dict = {}
        for d in (ids_por_fonte[ra], ids_por_fonte[rb]):
            for fonte_, ids in d.items():
                juntos.setdefault(fonte_, set()).update(ids)
        ids_por_fonte[novo] = juntos
        cpfs_validos[novo] = cpfs_validos[ra] | cpfs_validos[rb]

    def por_chave(chave_de, motivo, por_nome=False):
        primeiro: dict = {}
        for i, r in enumerate(registros):
            chave = chave_de(r)
            if chave is None:
                continue
            if chave in primeiro:
                unir(i, primeiro[chave], motivo, por_nome)
            else:
                primeiro[chave] = i

    # Ordem = força da evidência.
    por_chave(lambda r: (r["fonte"], r["id"]) if r["id"] else None, "mesmo ID na mesma fonte")
    por_id: dict[str, list[int]] = {}
    for i, r in enumerate(registros):
        if r["id"]:
            por_id.setdefault(r["id"], []).append(i)
    for indices in por_id.values():  # mesmo ID em fontes diferentes, se o nome não contradiz
        for a_ in indices[1:]:
            na, nb = registros[a_]["nome"], registros[indices[0]]["nome"]
            if not na or not nb or _nomes_compativeis(na, nb):
                unir(a_, indices[0], "mesmo ID em fontes diferentes")
    por_chave(lambda r: r["cpf"] if r["cpf_valido"] else None, "mesmo CPF")
    por_chave(lambda r: r["email"], "mesmo e-mail")
    por_chave(lambda r: v.chave_nome(r["nome"]) if r["nome"] and len(r["nome"].split()) >= 2 else None,
              "mesmo nome", por_nome=True)

    grupos: dict[int, list[int]] = {}
    for i in range(len(registros)):
        grupos.setdefault(uniao.achar(i), []).append(i)

    # Cada CPF válido "pertence" ao grupo onde mais aparece; nos outros vira conflito.
    dono_cpf: dict[str, int] = {}
    contagem_cpf: dict[str, dict[int, int]] = {}
    for g, membros in grupos.items():
        for i in membros:
            if registros[i]["cpf_valido"]:
                contagem_cpf.setdefault(registros[i]["cpf"], {}).setdefault(g, 0)
                contagem_cpf[registros[i]["cpf"]][g] += 1
    for cpf, por_grupo in contagem_cpf.items():
        dono_cpf[cpf] = max(por_grupo, key=por_grupo.get)

    # ---- 3. Índices para ligar transações ----
    indice: dict[tuple, int] = {}
    nomes_idx: dict[str, set[int]] = {}
    primeiros_nomes: dict[str, set[int]] = {}
    for g, membros in grupos.items():
        for i in membros:
            r = registros[i]
            if r["id"]:
                indice.setdefault(("id", r["id"]), g)
            if r["email"]:
                indice.setdefault(("email", r["email"]), g)
            if r["cpf"] and (not r["cpf_valido"] or dono_cpf.get(r["cpf"]) == g):
                indice.setdefault(("cpf", r["cpf"]), g)
            if r["nome"]:
                nomes_idx.setdefault(v.chave_nome(r["nome"]), set()).add(g)
                primeiros_nomes.setdefault(v.chave_nome(r["nome"]).split()[0], set()).add(g)

    def resolver(valor) -> tuple[int | None, str]:
        if v.vazio(valor):
            return None, ""
        texto = v.corrigir_mojibake(str(valor)).strip()
        if "@" in texto:
            email, _ = v.normalizar_email(texto)
            return indice.get(("email", email)), "e-mail"
        digitos = v.somente_digitos(texto)
        if digitos and len(digitos) == len(re.sub(r"[\s.\-/]", "", texto)):
            if len(digitos) >= 9 and (cpf := _cpf_digitos(digitos)):
                return indice.get(("cpf", cpf)), "CPF"
            return indice.get(("id", _id_texto(texto))), "ID"
        chave = v.chave_nome(texto)
        if chave in nomes_idx:
            if len(nomes_idx[chave]) > 1:
                return None, f"nome ambíguo: {len(nomes_idx[chave])} clientes homônimos"
            return next(iter(nomes_idx[chave])), "nome completo"
        candidatos = primeiros_nomes.get(chave.split()[0], set()) if chave else set()
        if len(candidatos) == 1 and len(chave.split()) == 1:
            return next(iter(candidatos)), "primeiro nome (único na base)"
        if len(candidatos) > 1:
            return None, "nome ambíguo"
        return None, "não encontrado"

    # ---- 4. Transações ----
    cpfs_extra: dict[int, list[tuple[str, str]]] = {}
    transacoes_saida: dict[str, list[dict]] = {}
    fontes_por_saida: dict[str, list[str]] = {}
    nao_vinculados = []
    for f in fontes.values():
        if f.tipo != "transacoes":
            continue
        df = tabelas[f.tabela]
        entidade = re.sub(r"s$", "", padronizar_nome_coluna(f.tabela).split("_")[0]) or "registro"
        ano = re.search(r"(?<!\d)(20\d{2})(?!\d)", f.tabela)
        nome_saida = f"{entidade}s" + (f"_{ano.group(1)}" if ano else "")
        preferencias = {c: v.preferencia_de_datas(df[c]) for c in f.colunas.get("data", [])}
        usadas = {c for cols in f.colunas.values() for c in cols}
        linhas = []
        for i, linha in df.iterrows():
            numero_linha = i + 2
            saida: dict = {f"{entidade}_id": _id_texto(_primeiro(f, "id", linha))}
            grupo, metodo, referencia = None, "", None
            for c in f.colunas.get("cliente_ref", []):
                valor = linha.get(c)
                if v.vazio(valor):
                    continue
                referencia = referencia or f"{c}={valor}"
                g, m = resolver(valor)
                if g is not None:
                    grupo, metodo, referencia = g, m, f"{c}={valor}"
                    break
                metodo = m
            # CPF citado na transação ajuda a corrigir o cadastro (ex.: CPF inválido no cadastro).
            for c in f.colunas.get("cliente_ref", []):
                cpf = _cpf_digitos(linha.get(c)) if re.search(r"cpf|doc", c) else None
                if grupo is not None and cpf:
                    cpfs_extra.setdefault(grupo, []).append((cpf, f"{f.tabela} linha {numero_linha}"))
            saida["_grupo"] = grupo
            saida["vinculo"] = metodo if grupo is not None else f"NÃO VINCULADO ({metodo})"
            saida["referencia_original"] = referencia
            if grupo is None:
                nao_vinculados.append({"tabela": f.tabela, "linha": numero_linha, "referencia": referencia, "motivo": metodo})
            for c in f.colunas.get("valor", [])[:1]:
                valor, moeda, alerta = v.interpretar_valor(linha.get(c))
                if valor is None and not alerta and linha.get(c) is not None and str(linha.get(c)).strip():
                    alerta = f"sem valor ('{str(linha.get(c)).strip()}')"
                saida["valor"], saida["moeda"] = valor, moeda
                corrigir(f.tabela, numero_linha, "valor", linha.get(c), valor, alerta)
            for c in f.colunas.get("data", [])[:1]:
                data, alerta = v.interpretar_data(linha.get(c), preferencias[c])
                saida["data"] = data.isoformat() if data else None
                corrigir(f.tabela, numero_linha, "data", linha.get(c), saida["data"], alerta)
            for c in f.colunas.get("pago", [])[:1]:
                pago = v.normalizar_booleano(linha.get(c))
                saida["pago"] = {True: "sim", False: "não", None: "não informado"}[pago]
                corrigir(f.tabela, numero_linha, "pago", linha.get(c), saida["pago"], "")
            if f.colunas.get("status"):
                bruto = _primeiro(f, "status", linha)
                saida["status"] = _normalizar_status(bruto)
                corrigir(f.tabela, numero_linha, "status", bruto, saida["status"], "")
            if f.colunas.get("itens"):
                saida["itens"] = _normalizar_itens(_primeiro(f, "itens", linha))
            for c in f.colunas.get("cartao", [])[:1]:
                bruto = linha.get(c)
                saida["cartao_final"] = f"**** {v.somente_digitos(bruto)[-4:]}" if not v.vazio(bruto) else None
            for c in df.columns:
                if c not in usadas and not v.vazio(linha.get(c)):
                    saida.setdefault(c, linha.get(c))
            linhas.append(saida)
        transacoes_saida[nome_saida] = transacoes_saida.get(nome_saida, []) + linhas
        fontes_por_saida.setdefault(nome_saida, []).append(f.tabela)

    # CPFs citados junto ao nome completo em documentos/logs (ex.: payload de log).
    cpf_re = re.compile(r"(?<![\d./-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d./-])")
    for origem, texto in textos or []:
        for n, linha_txt in enumerate(texto.splitlines(), start=1):
            cpfs = [v.somente_digitos(c) for c in cpf_re.findall(linha_txt)]
            if not cpfs:
                continue
            chave_linha = f" {_chave_texto(linha_txt)} "
            citados = {g for k, gs in nomes_idx.items() if len(k.split()) >= 2 and f" {k} " in chave_linha for g in gs}
            if len(citados) == 1:
                g = citados.pop()
                for cpf in cpfs:
                    if v.cpf_valido(cpf) and dono_cpf.get(cpf, g) == g:
                        cpfs_extra.setdefault(g, []).append((cpf, f"{origem} linha {n}"))
                        indice.setdefault(("cpf", cpf), g)

    # ---- 5. Registro de ouro de cada pessoa ----
    pessoas, conflitos = [], []
    for g, membros in grupos.items():
        ordem = sorted(membros, key=lambda i: (-registros[i]["prioridade"], -registros[i]["qualidade"], registros[i]["linha"]))
        rs = [registros[i] for i in ordem]
        ouro: dict = {"_grupo": g}
        ids = [r["id"] for r in rs if r["id"]]
        ouro["id_cliente"] = ids[0] if ids else None
        _conflito(conflitos, ouro, "id", [(r["id"], r) for r in rs if r["id"]], "fonte de maior prioridade")

        nomes = [r["nome"] for r in rs if r["nome"]]
        ouro["nome"] = nomes[0] if nomes else None

        candidatos = [(r["cpf"], f'{r["fonte"]} linha {r["linha"]}') for r in rs if r["cpf"]] + cpfs_extra.get(g, [])
        alheios = [(c, o) for c, o in candidatos if v.cpf_valido(c) and dono_cpf.get(c, g) != g]
        validos = [(c, origem) for c, origem in candidatos if v.cpf_valido(c) and dono_cpf.get(c, g) == g]
        if validos:
            ouro["cpf"] = v.formatar_cpf(validos[0][0])
            principal = rs[0]["cpf"]
            if principal and not v.cpf_valido(principal):
                ouro["cpf_status"] = f"corrigido: cadastro principal tinha CPF inválido; válido encontrado em {validos[0][1]}"
            else:
                ouro["cpf_status"] = "válido"
        elif candidatos:
            ouro["cpf"] = v.formatar_cpf(candidatos[0][0])
            ouro["cpf_status"] = "INVÁLIDO em todas as fontes — conferir com o titular"
        else:
            ouro["cpf"], ouro["cpf_status"] = None, "sem CPF"
        for c, o in alheios:
            dono = next((registros[i] for i in grupos[dono_cpf[c]] if registros[i]["id"]), None)
            conflitos.append({
                "id_cliente": ouro["id_cliente"], "nome": ouro["nome"], "campo": "cpf",
                "valor_escolhido": ouro["cpf"],
                "valores_encontrados": f"{v.formatar_cpf(c)} ({o})",
                "motivo": "CPF trocado: pertence a outro cliente"
                          + (f" (id {dono['id']}, {dono['nome']})" if dono else "") + " — NÃO usado",
            })
        distintos = {c for c, _ in candidatos if (c, _) not in alheios}
        if len(distintos) > 1:
            conflitos.append({
                "id_cliente": ouro["id_cliente"], "nome": ouro["nome"], "campo": "cpf",
                "valor_escolhido": ouro["cpf"],
                "valores_encontrados": "; ".join(f"{v.formatar_cpf(c)} ({'válido' if v.cpf_valido(c) else 'inválido'}, {o})" for c, o in candidatos),
                "motivo": "mantido o CPF com dígito verificador válido",
            })

        for campo in ("email", "telefone", "cidade", "nascimento"):
            opcoes = [(r.get(campo), r) for r in rs if r.get(campo) is not None]
            if campo == "telefone":
                # Telefone completo (com DDD) vence a versão sem DDD do mesmo número.
                completos = [o for o in opcoes if o[0].startswith("+55")]
                opcoes = completos + [o for o in opcoes if not o[0].startswith("+55")
                                      and not any(c[0].replace("-", "").endswith(o[0].replace("-", "")) for c in completos)]
            ouro[campo] = opcoes[0][0] if opcoes else None
            _conflito(conflitos, ouro, campo, opcoes, "fonte de maior prioridade / registro mais completo")
        ouro["data_nascimento"] = ouro.pop("nascimento").isoformat() if ouro.get("nascimento") else None
        inativos = [r for r in rs if r["inativo"]]
        ouro["ativo"] = "não" if inativos else "sim"
        ouro["registros_unificados"] = len(rs)
        ouro["fontes"] = ", ".join(sorted({r["fonte"] for r in rs}))
        obs = []
        if len([r for r in rs if r["fonte"] == rs[0]["fonte"]]) > 1:
            obs.append(f"{len([r for r in rs if r['fonte'] == rs[0]['fonte']])} linhas duplicadas na fonte principal")
        if any(r["senha_exposta"] for r in rs):
            obs.append("senha em texto puro na origem (removida)")
        if inativos:
            obs.append(f"cadastro marcado como '{inativos[0]['status_original']}' na origem (exclusão lógica) — mantido como inativo")
        variantes = sorted({r["id_variante"] for r in rs if r["id_variante"]})
        if variantes:
            obs.append("também cadastrado com o código " + ", ".join(variantes))
        if len({r["nome"] for r in rs if r["nome"]}) > 1:
            obs.append("nome escrito de formas diferentes: " + " / ".join(sorted({r["nome"] for r in rs if r["nome"]})))
        if ouro.get("email") and not ouro["email"].isascii():
            obs.append("e-mail com acento — pode ser rejeitado por outros sistemas; confirmar com o titular")
        ouro["observacoes"] = "; ".join(obs)
        pessoas.append(ouro)

    pessoas_df = pd.DataFrame(pessoas)
    pessoas_df["_ordem"] = pd.to_numeric(pessoas_df["id_cliente"], errors="coerce")
    pessoas_df = pessoas_df.sort_values(["_ordem", "nome"], na_position="last").drop(columns="_ordem")
    id_por_grupo = dict(zip(pessoas_df["_grupo"], pessoas_df["id_cliente"]))
    nome_por_grupo = dict(zip(pessoas_df["_grupo"], pessoas_df["nome"]))
    pessoas_df = pessoas_df.drop(columns="_grupo").reset_index(drop=True)
    colunas = ["id_cliente", "nome", "cpf", "cpf_status", "email", "telefone", "cidade", "data_nascimento", "ativo",
               "registros_unificados", "fontes", "observacoes"]
    pessoas_df = pessoas_df[colunas]

    transacoes = {}
    for nome, linhas in transacoes_saida.items():
        df = pd.DataFrame(linhas)
        id_col = df.columns[0]
        df.insert(1, "id_cliente", df["_grupo"].map(id_por_grupo))
        df.insert(2, "nome_cliente", df["_grupo"].map(nome_por_grupo))
        df = df.drop(columns="_grupo")
        df[id_col] = df[id_col].astype("string")
        df.attrs["fontes"] = fontes_por_saida.get(nome, [])
        transacoes[nome] = df

    total_registros = len(registros)
    por_fonte = {}
    for f in de_pessoas:
        membros = [i for i, r in enumerate(registros) if r["fonte"] == f.tabela]
        por_fonte[f.tabela] = {
            "linhas": len(membros),
            "pessoas_distintas": len({uniao.achar(i) for i in membros}),
            "duplicatas_internas": len(membros) - len({uniao.achar(i) for i in membros}),
            "prioridade": f.prioridade,
            "avaliacao": f.motivo,
        }
    resumo = {
        "registros_de_pessoas_lidos": total_registros,
        "pessoas_unicas": len(pessoas_df),
        "por_fonte": por_fonte,
        "cpf_invalido_sem_correcao": int(pessoas_df["cpf_status"].str.startswith("INVÁLIDO").sum()),
        "cpf_corrigido_por_outra_fonte": int(pessoas_df["cpf_status"].str.startswith("corrigido").sum()),
        "transacoes": {n: {"linhas": len(d), "vinculadas": int(d["id_cliente"].notna().sum())} for n, d in transacoes.items()},
        "conflitos": len(conflitos),
        "formatos_encontrados": formatos,
        "senhas_em_texto_puro": sum(1 for r in registros if r["senha_exposta"]),
        "emails_com_acento": sorted(f"{e[:2]}***@{e.split('@', 1)[1]}" for e in pessoas_df["email"].dropna() if not e.isascii()),
    }
    fontes_df = pd.DataFrame([
        {"tabela": f.tabela, "caminho": f.caminho, "tipo": f.tipo, "prioridade": f.prioridade or None,
         "avaliacao": f.motivo, "papeis_das_colunas": json.dumps(f.colunas, ensure_ascii=False)}
        for f in fontes.values()
    ])
    def resolver_publico(valor):
        g, metodo = resolver(valor)
        return (id_por_grupo.get(g) if g is not None else None), metodo

    for b in bloqueios:
        ra, rb = registros[b["a"]], registros[b["b"]]
        if b["motivo"] == "mesmo nome":
            conflitos.append({"id_cliente": f"{ra['id']} / {rb['id']}", "nome": ra["nome"], "campo": "homônimos",
                              "valor_escolhido": "mantidos SEPARADOS",
                              "valores_encontrados": f"{ra['fonte']} linha {ra['linha']}; {rb['fonte']} linha {rb['linha']}",
                              "motivo": "mesmo nome, mas IDs/CPFs diferentes — pessoas distintas"})
    return ResultadoEntidades(
        pessoas=pessoas_df,
        conflitos=pd.DataFrame(conflitos),
        transacoes=transacoes,
        correcoes=_mascarar_correcoes(pd.DataFrame(correcoes)),
        fontes=fontes_df,
        nao_vinculados=pd.DataFrame(nao_vinculados),
        resumo=resumo,
        outros_cadastros=outros_cadastros,
        resolver=resolver_publico,
    )


def _chave_texto(texto: str) -> str:
    """Texto livre (log, JSON, e-mail) sem acento/pontuação, para achar nomes completos dentro dele."""
    return re.sub(r"[^a-z0-9]+", " ", v.remover_acentos(v.corrigir_mojibake(texto)).lower()).strip()


def _nomes_compativeis(a: str, b: str) -> bool:
    """'José Carlos Andrade' ~ 'Jose C. Andrade': mesmo primeiro e último nome; iniciais batem."""
    ka, kb = v.chave_nome(a).replace(".", " ").split(), v.chave_nome(b).replace(".", " ").split()
    if not ka or not kb:
        return True
    if ka == kb:
        return True
    if ka[0] != kb[0] or ka[-1] != kb[-1]:
        return False
    meio_a, meio_b = ka[1:-1], kb[1:-1]
    return all(x[0] == y[0] for x, y in zip(meio_a, meio_b))


def _cadastro_simples(f: Fonte, df: pd.DataFrame) -> pd.DataFrame:
    """Leads/colaboradores: normaliza contato e consentimento, sem misturar com clientes."""
    linhas = []
    consent = next((c for c in df.columns if re.search(r"consent|optin|opt_in|aceite|autoriza", c)), None)
    for _, linha in df.iterrows():
        email, _ = v.normalizar_email(_primeiro(f, "email", linha)) if f.colunas.get("email") else (None, "")
        tel, _ = v.normalizar_telefone(_primeiro(f, "telefone", linha), None, aceitar_sem_ddd=True) if f.colunas.get("telefone") else (None, "")
        item = {"nome": v.normalizar_nome(_primeiro(f, "nome", linha)), "email": email, "telefone": tel}
        if consent:
            b = v.normalizar_booleano(linha.get(consent))
            item["consentimento"] = {True: "sim", False: "não", None: "não informado"}[b]
            item["pode_contatar"] = "sim" if b else "NÃO (sem consentimento registrado — LGPD art. 7º, I)"
        linhas.append(item)
    out = pd.DataFrame(linhas)
    out.attrs["tipo_pessoa"] = f.tipo_pessoa
    return out


def _conflito(conflitos: list, ouro: dict, campo: str, opcoes: list, motivo: str):
    distintos = {}
    for valor, r in opcoes:
        chave = valor.isoformat() if isinstance(valor, date) else valor
        distintos.setdefault(chave, []).append(f'{r["fonte"]} linha {r["linha"]}')
    if len(distintos) > 1:
        escolhido = ouro.get(campo)
        escolhido = escolhido.isoformat() if isinstance(escolhido, date) else escolhido
        desatualizada = any(r.get("desatualizada") for valor, r in opcoes
                            if (valor.isoformat() if isinstance(valor, date) else valor) != escolhido)
        conflitos.append({
            "id_cliente": ouro.get("id_cliente"), "nome": ouro.get("nome"), "campo": campo,
            "valor_escolhido": escolhido,
            "valores_encontrados": "; ".join(f"{k} ({', '.join(o)})" for k, o in distintos.items()),
            "motivo": motivo + (" — fonte antiga descartada" if desatualizada else ""),
        })


def _normalizar_status(valor) -> str | None:
    if v.vazio(valor):
        return None
    texto = str(valor).strip().lower()
    duvida = texto.endswith("?")
    texto = texto.rstrip("?").strip()
    return f"{texto} (a confirmar)" if duvida else texto


def _normalizar_itens(valor) -> str | None:
    if v.vazio(valor):
        return None
    texto = str(valor)
    try:
        dados = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return texto.strip()
    if isinstance(dados, list):
        partes = []
        for item in dados:
            if isinstance(item, dict):
                nome = item.get("produto") or item.get("nome") or item.get("item") or "?"
                qtd = item.get("qtd") or item.get("quantidade") or 1
                partes.append(f"{nome} x{qtd}")
            else:
                partes.append(str(item))
        return "; ".join(partes)
    return texto


def _mascarar_correcoes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    def mascara(campo, valor):
        if campo == "cpf" and not v.vazio(valor):
            d = v.somente_digitos(valor)
            return f"***.{d[3:6]}.{d[6:9]}-**" if len(d) >= 9 else "***"
        return valor
    df["valor_original"] = [mascara(c, x) for c, x in zip(df["campo"], df["valor_original"])]
    df["valor_corrigido"] = [mascara(c, x) for c, x in zip(df["campo"], df["valor_corrigido"])]
    return df
