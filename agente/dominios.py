"""Domínios de negócio além de pessoas: fornecedores, produtos/estoque, vendas e pagamentos.

Tudo é reconhecido pelo PAPEL das colunas (não pelo nome do arquivo):
- fornecedores: tabela com CNPJ + razão social;
- catálogo de produtos: SKU + descrição + preço;
- movimentos de estoque: SKU + tipo (entrada/saída) + quantidade;
- vendas: código do pedido + cliente + valor/quantidade (de quantas fontes houver);
- pagamentos: código do pedido + valor + status de pagamento (aprovado/estornado...).

Vendas de fontes diferentes são DEDUPLICADAS pelo código do pedido (inclusive o mesmo pedido
com outro prefixo, ex.: WEB-00052 = PV00052); fontes manuais valem menos que sistemas.
"""

from __future__ import annotations

import re
from datetime import datetime
from statistics import median

import pandas as pd

from . import validadores as v
from .entidades import _colunas_padronizadas, papel

PAPEIS_EXTRA = {
    "canal": {"canal", "channel", "origem_venda", "canal_venda"},
    "preco_unit": {"preco_unit", "preco_unitario", "unit_price", "valor_unitario", "vl_unitario"},
    "razao": {"razao", "razao_social", "nome_fantasia", "fornecedor_nome", "empresa"},
    "cnpj": {"cnpj", "cnpj_fornecedor", "nr_cnpj"},
    "descricao": {"descricao", "nome_produto", "produto_nome", "descricao_produto", "nome"},
    "categoria": {"categoria", "category", "grupo"},
    "peso": {"peso", "weight", "peso_bruto"},
    "unidade_peso": {"unidade_peso", "un_peso", "unidade", "weight_unit"},
    "fornecedor": {"fornecedor", "cod_fornecedor", "fornecedor_id", "supplier"},
    "deletado": {"deletado", "excluido", "removido", "deleted", "ativo", "inativo"},
    "tipo_mov": {"tipo", "tipo_movimento", "movimento", "operacao", "es"},
    "status": {"status", "situacao", "state"},
    "moeda": {"moeda", "currency", "amount_currency"},
}
STATUS_PAGAMENTO = re.compile(r"approved|aprovad|refund|estorn|chargeback|contesta|pago|paid|recusad|declined|pendente|pending", re.I)
FONTE_MANUAL = re.compile(r"manual|caixa|planilha|controle|rascunho|backup|c[óo]pia|copy|antig|old|nao usar|não usar", re.I)


def _papeis(df: pd.DataFrame) -> dict[str, list[str]]:
    colunas: dict[str, list[str]] = {}
    for c in df.columns:
        base = re.sub(r"_\d+$", "", c)
        achou = next((nome for nome, sin in PAPEIS_EXTRA.items() if base in sin), None)
        p = achou or papel(c)
        if p == "cpf" and base.startswith("cpf_cnpj") is False and "cnpj" in base:
            p = "cnpj"
        if p:
            colunas.setdefault(p, []).append(c)
    return colunas


def _um(linha, cols):
    for c in cols or []:
        x = linha.get(c)
        if not v.vazio(x):
            return x
    return None


def _ano_do_nome(texto: str) -> int | None:
    anos = re.findall(r"(?<!\d)(20\d{2})(?!\d)", texto)
    return int(anos[-1]) if anos else None


def construir(brutas: dict[str, pd.DataFrame], caminhos: dict[str, str], entidades) -> tuple[dict, dict]:
    tabelas = {n: _colunas_padronizadas(df) for n, df in brutas.items()}
    papeis = {n: _papeis(df) for n, df in tabelas.items()}
    saida: dict[str, pd.DataFrame] = {}
    resumo: dict = {}

    # ---------- Fornecedores ----------
    linhas_f = []
    for n, df in tabelas.items():
        p = papeis[n]
        if not (p.get("cnpj") and p.get("razao")):
            continue
        for i, linha in df.iterrows():
            cnpj = v.somente_digitos(_um(linha, p["cnpj"]) or "")
            linhas_f.append({
                "codigo": _um(linha, p.get("id")), "razao": _um(linha, p["razao"]), "cnpj": cnpj,
                "email": next((linha[c] for c in df.columns if "email" in c and not v.vazio(linha.get(c))), None),
                "responsavel": next((linha[c] for c in df.columns if "responsavel" in c and "cpf" not in c
                                     and not v.vazio(linha.get(c))), None),
                "fonte": n, "linha": i + 2,
            })
    if linhas_f:
        brutos = pd.DataFrame(linhas_f)
        brutos["_chave"] = brutos["codigo"].fillna(brutos["cnpj"])
        forn = []
        for chave, grupo in brutos.groupby("_chave", sort=True):
            grupo = grupo.assign(_completo=grupo.notna().sum(axis=1),
                                 _caixa=grupo["razao"].fillna("").map(lambda r: r.isupper()))
            melhor = grupo.sort_values(["_caixa", "_completo"], ascending=[True, False]).iloc[0]
            cnpj = melhor["cnpj"]
            obs = []
            if len(grupo) > 1:
                obs.append(f"{len(grupo)} registros na origem (duplicado) — mantido o mais completo")
            if len({c for c in grupo["cnpj"] if c}) > 1:
                obs.append("CNPJs diferentes para o mesmo código: " + ", ".join(sorted(set(grupo["cnpj"]))))
            valido = v.cnpj_valido(cnpj) if cnpj else False
            if not valido:
                obs.append("CNPJ INVÁLIDO (dígito verificador) — conferir na Receita Federal")
            forn.append({
                "codigo": melhor["codigo"], "razao_social": v.normalizar_nome(melhor["razao"]).replace(" Ltda", " Ltda")
                if melhor["razao"] and melhor["razao"].isupper() else melhor["razao"],
                "cnpj": v.formatar_cnpj(cnpj) if cnpj else None, "cnpj_valido": "sim" if valido else "não",
                "email_contato": melhor["email"], "responsavel": melhor["responsavel"],
                "registros_na_origem": len(grupo), "observacoes": "; ".join(obs),
            })
        saida["fornecedores"] = pd.DataFrame(forn)
        resumo["fornecedores"] = {"total": len(forn), "duplicados": int((saida["fornecedores"]["registros_na_origem"] > 1).sum()),
                                  "cnpj_invalido": saida["fornecedores"].loc[saida["fornecedores"]["cnpj_valido"] == "não", "codigo"].tolist()}

    # ---------- Catálogo de produtos ----------
    catalogo: dict[str, dict] = {}
    nome_para_sku: dict[str, str] = {}
    excluidos_prod = []
    for n, df in tabelas.items():
        p = papeis[n]
        if not (p.get("sku") and (p.get("descricao") or p.get("valor"))) or p.get("tipo_mov") or p.get("cliente_ref"):
            continue
        for i, linha in df.iterrows():
            sku = str(_um(linha, p["sku"]) or "").strip().upper()
            if not sku:
                continue
            deletado = _um(linha, p.get("deletado"))
            col_del = (p.get("deletado") or [""])[0]
            apagado = (str(deletado).strip().lower() in {"1", "true", "sim", "s", "yes"}) if "ativo" not in col_del else \
                (str(deletado).strip().lower() in {"0", "false", "nao", "não", "n", "no"})
            preco, alerta_preco = v.interpretar_preco(_um(linha, p.get("valor")))
            peso, alerta_peso = v.converter_peso_kg(_um(linha, p.get("peso")), _um(linha, p.get("unidade_peso")))
            item = {"sku": sku, "nome": v.corrigir_mojibake(str(_um(linha, p.get("descricao")) or "")),
                    "categoria": _um(linha, p.get("categoria")), "preco_brl": round(preco, 2) if preco is not None else None,
                    "peso_kg": round(peso, 3) if peso is not None else None, "fornecedor": _um(linha, p.get("fornecedor")),
                    "alertas": "; ".join(a for a in (alerta_preco, alerta_peso) if a), "fonte": n}
            if apagado:
                excluidos_prod.append({**item, "motivo": "registro marcado como deletado na origem — ignorado"})
                continue
            if sku in catalogo:
                excluidos_prod.append({**item, "motivo": f"SKU repetido — mantido o primeiro ({catalogo[sku]['fonte']})"})
                continue
            catalogo[sku] = item
            nome_para_sku[v.chave_nome(item["nome"])] = sku

    # ---------- Movimentos de estoque ----------
    movs = []
    for n, df in tabelas.items():
        p = papeis[n]
        if not (p.get("sku") and p.get("tipo_mov") and p.get("qtd")):
            continue
        for i, linha in df.iterrows():
            sku = str(_um(linha, p["sku"]) or "").strip().upper()
            tipo_bruto = str(_um(linha, p["tipo_mov"]) or "")
            t = v.remover_acentos(tipo_bruto).lower()
            tipo = "entrada" if re.search(r"entr|compra|devol|in\b|^e$", t) else "saida" if re.search(r"said|venda|baixa|out\b|^s$", t) else None
            qtd = v.converter_valor_monetario(_um(linha, p["qtd"]))
            data, _, alerta_data = v.interpretar_data_hora(_um(linha, p.get("data")))
            alertas = []
            if tipo is None:
                alertas.append(f"tipo de movimento desconhecido ({tipo_bruto})")
            if sku not in catalogo:
                alertas.append("SKU inexistente no catálogo")
            if data and data > datetime.now():
                alertas.append(f"data no futuro ({data:%d/%m/%Y})")
            movs.append({"sku": sku, "tipo": tipo, "tipo_original": tipo_bruto, "qtd": qtd,
                         "data": data.date().isoformat() if data else None, "alertas": "; ".join(alertas), "fonte": n, "linha": i + 2})
    if movs:
        mov_df = pd.DataFrame(movs)
        saida["movimentos_estoque"] = mov_df
        validos = mov_df[(mov_df["alertas"] == "") & mov_df["tipo"].notna()]
        sinal = validos["qtd"].where(validos["tipo"] == "entrada", -validos["qtd"])
        saldo = sinal.groupby(validos["sku"]).sum()
        entradas = validos[validos["tipo"] == "entrada"].groupby("sku")["qtd"].sum()
        saidas = validos[validos["tipo"] == "saida"].groupby("sku")["qtd"].sum()
        for sku, item in catalogo.items():
            item["entradas"] = int(entradas.get(sku, 0))
            item["saidas"] = int(saidas.get(sku, 0))
            item["saldo_estoque"] = int(saldo.get(sku, 0))
            if item["saldo_estoque"] < 0:
                item["alertas"] = "; ".join(a for a in (item["alertas"], "SALDO NEGATIVO — saídas maiores que entradas (inconsistência)") if a)
        problemas = mov_df[mov_df["alertas"] != ""]
        resumo["estoque"] = {
            "movimentos": len(mov_df), "ignorados": len(problemas),
            "movimentos_ignorados": [f"{r.sku} linha {r.linha}: {r.alertas}" for r in problemas.itertuples()],
            "tipos_encontrados": sorted(set(mov_df["tipo_original"])),
        }
    if catalogo:
        prod = pd.DataFrame(catalogo.values()).drop(columns="fonte")
        saida["produtos"] = prod
        resumo["produtos"] = {
            "total": len(prod), "excluidos": [f"{e['sku']}: {e['motivo']}" for e in excluidos_prod],
            "saldo_negativo": sorted(prod.loc[prod.get("saldo_estoque", pd.Series(dtype=int)) < 0, "sku"]) if "saldo_estoque" in prod else [],
        }

    # ---------- Vendas (todas as fontes) ----------
    resolver = entidades.resolver if entidades is not None else (lambda x: (None, "sem base de clientes"))
    fontes_venda = [n for n, p in papeis.items()
                    if p.get("id") and p.get("cliente_ref") and (p.get("valor") or p.get("qtd"))]
    candidatos = []
    for n in fontes_venda:
        df, p = tabelas[n], papeis[n]
        caminho = caminhos.get(n, n)
        manual = bool(FONTE_MANUAL.search(caminho))
        ano = _ano_do_nome(caminho)
        utc = any("utc" in c for c in p.get("data", []))
        pref = {c: v.preferencia_de_datas(df[c]) for c in p.get("data", [])}
        # Valor em centavos? Inteiros sem casas decimais cuja razão p/ qtd × preço do catálogo ≈ 100.
        centavos, razoes = False, []
        col_valor = (p.get("valor") or [None])[0]
        if col_valor:
            inteiros = df[col_valor].dropna().astype(str).str.fullmatch(r"\d+").mean() > 0.95
            if inteiros:
                for _, linha in df.iterrows():
                    sku = _resolver_sku(_um(linha, p.get("sku") or p.get("itens")), catalogo, nome_para_sku)
                    qtd = v.converter_valor_monetario(_um(linha, p.get("qtd")))
                    bruto = v.converter_valor_monetario(linha.get(col_valor))
                    if sku and qtd and bruto and catalogo[sku]["preco_brl"]:
                        razoes.append(bruto / (qtd * catalogo[sku]["preco_brl"]))
                centavos = bool(razoes) and 95 <= median(razoes) <= 105 or "cent" in col_valor
        for i, linha in df.iterrows():
            pedido = str(_um(linha, p["id"]) or "").strip().upper()
            alertas = []
            id_cliente, metodo, ref = None, "", None
            for c in p.get("cliente_ref", []):
                x = linha.get(c)
                if v.vazio(x):
                    continue
                ref = ref or f"{x}"
                id_cliente, metodo = resolver(x)
                if id_cliente is not None:
                    ref = f"{x}"
                    break
            sku = _resolver_sku(_um(linha, p.get("sku") or p.get("itens")), catalogo, nome_para_sku)
            qtd = v.converter_valor_monetario(_um(linha, p.get("qtd")))
            total = v.converter_valor_monetario(linha.get(col_valor)) if col_valor else None
            if total is not None and centavos:
                total = total / 100
                alertas.append("valor em centavos convertido para reais")
            unit = v.converter_valor_monetario(_um(linha, p.get("preco_unit")))
            if total is None and unit is not None and qtd:
                total = unit * qtd
                alertas.append("total calculado (qtd × preço unitário)")
            data, tem_hora, alerta_data = v.interpretar_data_hora(_um(linha, p.get("data")), pref.get((p.get("data") or [None])[0], "DM"),
                                                                  utc=utc, ano_padrao=ano)
            if alerta_data:
                alertas.append(alerta_data)
            moeda = str(_um(linha, p.get("moeda")) or "BRL").upper()
            candidatos.append({
                "pedido": pedido, "id_cliente": id_cliente, "sku": sku, "qtd": int(qtd) if qtd is not None and float(qtd).is_integer() else qtd,
                "total_brl": round(total, 2) if total is not None else None,
                "data_hora_brasilia": (data.strftime("%Y-%m-%d %H:%M") if tem_hora else data.strftime("%Y-%m-%d")) if data else None,
                "canal": (str(_um(linha, p.get("canal"))).strip().lower() if _um(linha, p.get("canal")) else None),
                "vinculo": metodo, "cliente_original": ref, "fonte": n, "linha": i + 2, "manual": manual,
                "alertas": "; ".join(alertas), "moeda": moeda, "_tem_hora": tem_hora,
                "_data_invalida": data is None,
            })
    if candidatos:
        cand = pd.DataFrame(candidatos)
        cand["_num"] = cand["pedido"].str.extract(r"(\d+)$")[0]
        cand["_prefixo"] = cand["pedido"].str.extract(r"^([A-Z]*)")[0]
        prefixo_principal = cand["_prefixo"].mode().iloc[0]
        validas, excluidas = [], []
        # Prioridade: sistema > manual; com hora > sem; mais campos preenchidos.
        cand["_rank"] = (cand["manual"].astype(int) * 10 + (~cand["_tem_hora"]).astype(int)
                         + cand[["sku", "qtd", "canal", "data_hora_brasilia"]].isna().sum(axis=1))
        cand = cand.sort_values(["_rank", "fonte", "linha"])
        vistos: dict[str, dict] = {}
        for r in cand.to_dict("records"):
            chave = r["pedido"]
            if r["_prefixo"] != prefixo_principal and r["_num"]:
                gemeo = cand[(cand["_num"] == r["_num"]) & (cand["_prefixo"] == prefixo_principal)]
                if len(gemeo):
                    g = gemeo.iloc[0]
                    mesmo = (abs((g["total_brl"] or 0) - (r["total_brl"] or 0)) < 0.01) and g["id_cliente"] == r["id_cliente"]
                    if mesmo:
                        excluidas.append({**r, "motivo": f"DUPLICATA: mesmo pedido de {g['pedido']} com outro código ({r['pedido']})"})
                        continue
            if chave in vistos:
                original = vistos[chave]
                diverg = "" if abs((original["total_brl"] or 0) - (r["total_brl"] or 0)) < 0.01 else \
                    f" — valor diferente ({r['total_brl']} × {original['total_brl']})"
                excluidas.append({**r, "motivo": f"já lançada em {original['fonte']} (não somada de novo){diverg}"})
                if not original.get("canal") and r.get("canal"):
                    original["canal"] = r["canal"]
                continue
            problemas = []
            if pd.isna(r["id_cliente"]) and "ambíguo" not in r["vinculo"]:
                problemas.append(f"cliente inexistente ({r['cliente_original']})")
            if r["_data_invalida"]:
                problemas.append("data inválida/ausente")
            if r["total_brl"] is None:
                problemas.append("sem valor")
            if r["moeda"] != "BRL":
                problemas.append(f"moeda {r['moeda']} sem cotação")
            if problemas:
                excluidas.append({**r, "motivo": "ÓRFÃ/INVÁLIDA: " + "; ".join(problemas)})
                continue
            if "ambíguo" in r["vinculo"]:
                r["alertas"] = "; ".join(a for a in (r["alertas"], f"cliente AMBÍGUO: '{r['cliente_original']}' ({r['vinculo']}) — não atribuído") if a)
            vistos[chave] = r
            validas.append(r)
        colunas = ["pedido", "id_cliente", "sku", "qtd", "total_brl", "data_hora_brasilia", "canal", "fonte", "vinculo",
                   "cliente_original", "alertas"]
        vendas = pd.DataFrame(validas)[colunas].sort_values("pedido").reset_index(drop=True)
        vendas["qtd"] = pd.to_numeric(vendas["qtd"], errors="coerce").round().astype("Int64")
        vendas["id_cliente"] = vendas["id_cliente"].astype("string")
        # Outliers de valor (ex.: 999.999 digitado) entre as válidas: só sinaliza.
        if len(vendas) > 10:
            limite = vendas["total_brl"].median() * 50
            fora = vendas["total_brl"] > limite
            vendas.loc[fora, "alertas"] = vendas.loc[fora, "alertas"] + "; valor fora do padrão (>50× a mediana)"
        excl = pd.DataFrame(excluidas)
        excl = excl[[c for c in ["pedido", "motivo", "id_cliente", "total_brl", "data_hora_brasilia", "fonte", "linha", "cliente_original"]
                     if c in excl.columns]] if len(excl) else excl
        saida["vendas"] = vendas
        saida["vendas_excluidas"] = excl
        por_ano = vendas.assign(ano=vendas["data_hora_brasilia"].str[:4]).groupby("ano")["total_brl"].agg(["count", "sum"])
        resumo["vendas"] = {
            "fontes": fontes_venda, "linhas_lidas": len(cand), "vendas_validas": len(vendas),
            "receita_bruta_brl": round(float(vendas["total_brl"].sum()), 2),
            "por_ano": {a: {"vendas": int(r["count"]), "receita": round(float(r["sum"]), 2)} for a, r in por_ano.iterrows()},
            "duplicatas_outro_codigo": int(excl["motivo"].str.startswith("DUPLICATA").sum()) if len(excl) else 0,
            "ja_lancadas_em_outra_fonte": int(excl["motivo"].str.startswith("já lançada").sum()) if len(excl) else 0,
            "orfas_invalidas": excl[excl["motivo"].str.startswith("ÓRFÃ")][["pedido", "motivo"]].to_dict("records") if len(excl) else [],
            "ambiguas": vendas[vendas["alertas"].str.contains("AMBÍGUO")]["pedido"].tolist(),
            "centavos_convertidos": int(vendas["alertas"].str.contains("centavos").sum()),
            "utc_convertidos": int(vendas["alertas"].str.contains("UTC").sum()),
        }

    # ---------- Pagamentos × vendas (conciliação) ----------
    vendas_idx = {r["pedido"]: r for r in saida["vendas"].to_dict("records")} if "vendas" in saida else {}
    conc = []
    for n, df in tabelas.items():
        p = papeis[n]
        if n in fontes_venda or not (p.get("id") and p.get("valor") and p.get("status")):
            continue
        st = df[p["status"][0]].dropna().astype(str)
        if not len(st) or st.map(lambda x: bool(STATUS_PAGAMENTO.search(x))).mean() < 0.8:
            continue
        for i, linha in df.iterrows():
            pedido = str(_um(linha, p["id"]) or "").strip().upper()
            valor = v.converter_valor_monetario(_um(linha, p["valor"]))
            status_bruto = str(_um(linha, p["status"]) or "")
            s_ = status_bruto.lower()
            status = ("estornado" if re.search(r"refund|estorn", s_) else "chargeback" if re.search(r"chargeback|contesta", s_)
                      else "aprovado" if re.search(r"approved|aprovad|pago|paid", s_) else s_)
            venda = vendas_idx.get(pedido)
            if venda is None:
                situacao = "pagamento SEM venda correspondente"
            elif valor is not None and abs(valor - venda["total_brl"]) > 0.01:
                situacao = f"valor divergente: pago {valor:.2f} × venda {venda['total_brl']:.2f}"
            else:
                situacao = "ok"
            conc.append({"pedido": pedido, "status_pagamento": status, "valor_pago": valor,
                         "valor_venda": venda["total_brl"] if venda else None, "situacao": situacao, "fonte": n, "linha": i + 2})
    if conc:
        co = pd.DataFrame(conc)
        saida["conciliacao"] = co
        resumo["conciliacao"] = {
            "pagamentos": len(co),
            "divergentes": co[co["situacao"].str.startswith("valor divergente")]["pedido"].tolist(),
            "sem_venda": co[co["situacao"].str.startswith("pagamento SEM")]["pedido"].tolist(),
            "estornos_brl": round(float(co.loc[co["status_pagamento"] == "estornado", "valor_pago"].sum()), 2),
            "chargebacks_brl": round(float(co.loc[co["status_pagamento"] == "chargeback", "valor_pago"].sum()), 2),
            "qtd_estornos": int((co["status_pagamento"] == "estornado").sum()),
            "qtd_chargebacks": int((co["status_pagamento"] == "chargeback").sum()),
        }
    return saida, resumo


def _resolver_sku(valor, catalogo: dict, nome_para_sku: dict) -> str | None:
    if v.vazio(valor):
        return None
    texto = v.corrigir_mojibake(str(valor)).strip()
    if texto.upper() in catalogo or re.fullmatch(r"[A-Z]{2,5}-?\d{2,}", texto.upper()):
        return texto.upper()
    return nome_para_sku.get(v.chave_nome(texto))
