"""Orquestra o agente: ler → entender → limpar → proteger → sincronizar → relatar."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import agente_ia
from . import sensiveis as sens
from . import validadores as v
from .documentos import EXTENSOES_DOCUMENTO, Documento, ler_documento, parece_texto_corrido
from .leitor import ler_arquivo
from .normalizador import ResultadoTabela, normalizar_tabela, perfil_tabela
from .organizador_docs import TIPOS_DOCUMENTO, encontrar_continuacao, fichar, herdar_partes, tabela_fichas
from .sincronizador import ResultadoSincronizacao, sincronizar

COLUNAS_SENSIVEIS = ["origem", "pagina_ou_coluna", "tipo", "categoria", "valor_mascarado", "contexto"]


@dataclass
class ResultadoAgente:
    tabelas: list[ResultadoTabela]
    sincronizacao: ResultadoSincronizacao
    dicionario: pd.DataFrame
    problemas: pd.DataFrame
    resumo: dict
    parecer: str
    descricoes: dict[str, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    documentos: list[Documento] = field(default_factory=list)
    fichas: pd.DataFrame = field(default_factory=pd.DataFrame)
    dossies: pd.DataFrame = field(default_factory=pd.DataFrame)
    vinculos: pd.DataFrame = field(default_factory=pd.DataFrame)
    lacunas: pd.DataFrame = field(default_factory=pd.DataFrame)
    sensiveis: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=COLUNAS_SENSIVEIS))
    risco: pd.DataFrame = field(default_factory=pd.DataFrame)
    cruzamento_docs_dados: pd.DataFrame = field(default_factory=pd.DataFrame)
    documentos_anonimizados: dict[str, str] = field(default_factory=dict)
    tabelas_anonimizadas: dict[str, pd.DataFrame] = field(default_factory=dict)


def _nome_unico(nome: str, usados) -> str:
    candidato, contador = nome, 2
    while candidato in usados:
        candidato = f"{nome}_{contador}"
        contador += 1
    return candidato


def executar(
    arquivos: list[tuple[str, bytes]],
    usar_ia: bool = False,
    chave_forcada: str | None = None,
    progresso: Callable[[str], None] | None = None,
) -> ResultadoAgente:
    log: list[str] = []

    def registrar(msg: str):
        log.append(msg)
        if progresso:
            progresso(msg)

    ia = usar_ia and agente_ia.ia_disponivel()
    if usar_ia and not ia:
        registrar("⚠️ ANTHROPIC_API_KEY não configurada; seguindo só com regras automáticas.")

    # 1. Leitura: planilhas viram tabelas; PDF/Word/HTML/TXT corrido viram documentos.
    brutas: dict[str, pd.DataFrame] = {}
    documentos: dict[str, Documento] = {}
    for nome, conteudo in arquivos:
        extensao = Path(nome).suffix.lower()
        try:
            if extensao in EXTENSOES_DOCUMENTO or (extensao == ".txt" and parece_texto_corrido(conteudo)):
                doc = ler_documento(nome, conteudo)
                doc.nome = _nome_unico(doc.nome, documentos)
                documentos[doc.nome] = doc
                registrar(f"📄 {doc.nome}: documento com {len(doc.paginas)} página(s), {len(doc.texto)} caracteres")
                for aviso in doc.avisos:
                    registrar(f"⚠️ {doc.nome}: {aviso}")
                for i, df in enumerate(doc.tabelas, start=1):
                    chave = _nome_unico(f"{doc.nome}__tabela{i}", brutas)
                    brutas[chave] = df
                    registrar(f"📥 {chave}: tabela extraída do documento ({len(df)} linhas)")
            else:
                for tabela, df in ler_arquivo(nome, conteudo).items():
                    chave = _nome_unico(tabela, brutas)
                    brutas[chave] = df
                    registrar(f"📥 {chave}: {len(df)} linhas × {len(df.columns)} colunas")
        except Exception as erro:  # arquivo corrompido não deve derrubar os demais
            registrar(f"⚠️ Não foi possível ler '{nome}': {erro}")
    if not brutas and not documentos:
        raise ValueError("Nenhum arquivo pôde ser lido.")

    # 2. Entendimento das colunas pela IA (opcional)
    mapeamento: dict = {}
    if ia and brutas:
        registrar("🤖 IA analisando o significado das colunas...")
        try:
            mapeamento = agente_ia.aplicar_mapeamento(agente_ia.mapear_colunas(brutas))
            registrar("🤖 Colunas mapeadas para nomes padronizados.")
        except Exception as erro:
            registrar(f"⚠️ IA indisponível ({erro}); seguindo só com regras automáticas.")

    # 3. Limpeza e padronização das tabelas
    tabelas = []
    descricoes = {}
    for nome, df in brutas.items():
        renomear, tipos, descricao = mapeamento.get(nome, ({}, {}, ""))
        resultado = normalizar_tabela(nome, df, renomear=renomear, tipos_forcados=tipos)
        tabelas.append(resultado)
        if descricao:
            descricoes[nome] = descricao
        registrar(
            f"🧹 {nome}: {len(resultado.problemas)} problema(s), "
            f"{resultado.duplicadas_removidas} duplicata(s) removida(s)"
        )

    # 4. Dados pessoais e sensíveis (LGPD)
    achados: list[dict] = []
    tabelas_anonimizadas = {}
    for t in tabelas:
        da_tabela = sens.encontrar_em_tabela(t.nome, t.dados, t.tipos)
        achados += da_tabela
        tabelas_anonimizadas[t.nome] = sens.anonimizar_tabela(t.dados, da_tabela)
    documentos_anonimizados = {}
    for doc in documentos.values():
        achados += sens.encontrar_em_texto(doc.texto, doc.nome, doc.paginas)
        documentos_anonimizados[doc.nome] = sens.anonimizar_texto(doc.texto)
    sensiveis = pd.DataFrame(achados) if achados else pd.DataFrame(columns=COLUNAS_SENSIVEIS + ["_valor"])
    risco = _risco_por_origem(sensiveis, list(brutas) + list(documentos))
    if len(sensiveis):
        registrar(
            f"🔒 {int((sensiveis['categoria'] != 'empresa').sum())} ocorrência(s) de dados pessoais/sensíveis; "
            f"{int((risco['risco'] == 'ALTO').sum())} arquivo(s) com risco ALTO."
        )

    # 5. Organização dos documentos e continuação entre eles
    fichas_obj = [fichar(d) for d in documentos.values()]
    herdar_partes(fichas_obj)
    if ia and fichas_obj:
        tipos_validos = [t for t, _, _ in TIPOS_DOCUMENTO]
        for ficha in fichas_obj:
            registrar(f"🤖 IA lendo '{ficha.nome}'...")
            try:
                analise = agente_ia.analisar_documento(ficha.nome, documentos_anonimizados[ficha.nome], tipos_validos)
            except Exception as erro:
                registrar(f"⚠️ IA não analisou '{ficha.nome}' ({erro}).")
                continue
            if analise.pop("_truncado", False):
                registrar(f"⚠️ '{ficha.nome}' é muito longo: a IA leu só o início.")
            ficha.resumo = analise["resumo"]
            if analise["pontos_de_atencao"]:
                ficha.resumo += "\nPontos de atenção: " + "; ".join(analise["pontos_de_atencao"])
            ficha.orgao = ficha.orgao or analise["orgao"]
            ficha.objeto = ficha.objeto or analise["objeto"]
            if ficha.tipo == "Outro" and analise["tipo"] in tipos_validos:
                ficha.tipo = analise["tipo"]
                ficha.etapa = next(e for t, e, _ in TIPOS_DOCUMENTO if t == analise["tipo"])
    fichas = tabela_fichas(fichas_obj)
    dossies = vinculos = lacunas = pd.DataFrame()
    if fichas_obj:
        dossies, vinculos, lacunas = encontrar_continuacao(fichas_obj, documentos)
        registrar(
            f"🗂️ {len(documentos)} documento(s) organizados em {dossies['dossie'].nunique()} dossiê(s); "
            f"{len(vinculos)} vínculo(s) de continuação; {len(lacunas)} lacuna(s)."
        )

    # 6. Sincronização das tabelas
    sinc = sincronizar(tabelas, chave_forcada=chave_forcada)
    for obs in sinc.observacoes:
        registrar(f"🔗 {obs}")
    cruzamento = _cruzar_documentos_e_dados(sensiveis, fichas_obj, tabelas)
    if len(cruzamento):
        registrar(f"🔗 {len(cruzamento)} CPF/CNPJ citado(s) em documentos também aparece(m) nas tabelas.")

    dicionario = pd.concat([perfil_tabela(t) for t in tabelas], ignore_index=True) if tabelas else pd.DataFrame()
    if len(dicionario):
        marcas = {(a["origem"], a["pagina_ou_coluna"]): f"{a['categoria']}: {a['tipo']}" for a in achados}
        dicionario["dado_pessoal_lgpd"] = [marcas.get((t, c), "") for t, c in zip(dicionario["tabela"], dicionario["coluna"])]
    problemas = pd.DataFrame([p for t in tabelas for p in t.problemas])
    if problemas.empty:
        problemas = pd.DataFrame(columns=["tabela", "linha", "coluna", "valor_original", "problema"])

    resumo = montar_resumo(tabelas, sinc, problemas, descricoes, fichas, dossies, lacunas, sensiveis, risco)
    parecer = parecer_automatico(resumo)
    if ia:
        registrar("🤖 IA redigindo o parecer...")
        try:
            parecer = agente_ia.redigir_parecer(resumo)
        except Exception as erro:
            registrar(f"⚠️ Parecer da IA falhou ({erro}); usando parecer automático.")

    registrar("✅ Processamento concluído.")
    return ResultadoAgente(
        tabelas, sinc, dicionario, problemas, resumo, parecer, descricoes, log,
        documentos=list(documentos.values()),
        fichas=fichas,
        dossies=dossies,
        vinculos=vinculos,
        lacunas=lacunas,
        sensiveis=sensiveis.drop(columns=["_valor"], errors="ignore"),
        risco=risco,
        cruzamento_docs_dados=cruzamento,
        documentos_anonimizados=documentos_anonimizados,
        tabelas_anonimizadas=tabelas_anonimizadas,
    )


def _risco_por_origem(sensiveis: pd.DataFrame, origens: list[str]) -> pd.DataFrame:
    linhas = []
    for origem in origens:
        doc = sensiveis[sensiveis["origem"] == origem] if len(sensiveis) else sensiveis
        registros = doc.to_dict("records")
        linhas.append({
            "arquivo": origem,
            "risco": sens.nivel_de_risco(registros),
            "dados_pessoais": int((doc["categoria"] == "pessoal").sum()) if len(doc) else 0,
            "dados_sensiveis": int((doc["categoria"] == "sensivel").sum()) if len(doc) else 0,
            "criancas_adolescentes": int((doc["categoria"] == "crianca").sum()) if len(doc) else 0,
            "tipos_encontrados": ", ".join(sorted(set(doc["tipo"]))) if len(doc) else "",
            "recomendacao": {
                "ALTO": "Restringir acesso; publicar só a versão anonimizada; verificar base legal (LGPD art. 11).",
                "MÉDIO": "Publicar com CPF mascarado e sem contatos/endereço (LGPD art. 6º, III).",
                "BAIXO": "Sem dados pessoais detectados.",
            }[sens.nivel_de_risco(registros)],
        })
    return pd.DataFrame(linhas)


def _cruzar_documentos_e_dados(sensiveis, fichas_obj, tabelas) -> pd.DataFrame:
    """CPFs/CNPJs citados nos documentos que também existem nas tabelas enviadas."""
    nas_tabelas: dict[str, set[str]] = {}
    for t in tabelas:
        for coluna, tipo in t.tipos.items():
            if tipo in {"cpf", "cnpj", "cpf_cnpj"}:
                for valor in t.dados[coluna].dropna().astype(str):
                    nas_tabelas.setdefault(v.somente_digitos(valor), set()).add(f"{t.nome}.{coluna}")
    if not nas_tabelas:
        return pd.DataFrame()
    linhas = []
    citados = []
    if len(sensiveis):
        citados += [(r["origem"], "CPF", r["_valor"]) for r in sensiveis.to_dict("records") if r["tipo"] == "CPF"]
    citados += [(f.nome, "CNPJ", c) for f in fichas_obj for c in f.cnpjs]
    vistos = set()
    for documento, tipo, valor in citados:
        digitos = v.somente_digitos(valor)
        if digitos in nas_tabelas and (documento, digitos) not in vistos:
            vistos.add((documento, digitos))
            linhas.append({
                "documento": documento,
                "tipo": tipo,
                "identificador": sens.mascarar(tipo, valor),
                "encontrado_em": ", ".join(sorted(nas_tabelas[digitos])),
            })
    return pd.DataFrame(linhas)


def montar_resumo(
    tabelas, sinc: ResultadoSincronizacao, problemas: pd.DataFrame, descricoes,
    fichas: pd.DataFrame, dossies: pd.DataFrame, lacunas: pd.DataFrame,
    sensiveis: pd.DataFrame, risco: pd.DataFrame,
) -> dict:
    """Estatísticas agregadas — sem dados pessoais — usadas no parecer e na aba Resumo."""
    por_tipo = problemas.groupby(["tabela", "problema"]).size().reset_index(name="qtd") if len(problemas) else None
    pessoais = sensiveis[sensiveis["categoria"] != "empresa"] if len(sensiveis) else sensiveis
    return {
        "documentos": [
            {k: r[k] for k in ("documento", "tipo", "etapa", "orgao", "numero", "data", "objeto", "valor_maior_citado")}
            for r in fichas.to_dict("records")
        ],
        "dossies": (
            dossies.groupby("dossie").agg(identificacao=("identificacao", "first"), documentos=("documento", list))
            .reset_index().to_dict("records") if len(dossies) else []
        ),
        "lacunas": lacunas.to_dict("records") if len(lacunas) else [],
        "dados_pessoais_lgpd": {
            "ocorrencias_por_tipo": pessoais["tipo"].value_counts().to_dict() if len(pessoais) else {},
            "risco_por_arquivo": risco[["arquivo", "risco"]].to_dict("records") if len(risco) else [],
        },
        "tabelas": [
            {
                "nome": t.nome,
                "descricao": descricoes.get(t.nome, ""),
                "linhas_originais": t.linhas_originais,
                "linhas_finais": len(t.dados),
                "duplicadas_removidas": t.duplicadas_removidas,
                "colunas": len(t.dados.columns),
                "tipos": t.tipos,
                "problemas": len(t.problemas),
            }
            for t in tabelas
        ],
        "problemas_por_tipo": [] if por_tipo is None else por_tipo.to_dict("records"),
        "sincronizacao": {
            "chave": sinc.chave,
            "tabelas_cruzadas": sinc.tabelas_usadas,
            "registros_consolidados": len(sinc.consolidado),
            "cobertura": sinc.cobertura.to_dict("records") if len(sinc.cobertura) else [],
            "divergencias": len(sinc.divergencias),
            "divergencias_por_campo": (
                sinc.divergencias["campo"].value_counts().to_dict() if len(sinc.divergencias) else {}
            ),
            "observacoes": sinc.observacoes,
        },
    }


def parecer_automatico(resumo: dict) -> str:
    linhas = ["# Parecer do processamento", ""]
    if resumo["documentos"]:
        linhas += ["## Documentos recebidos", ""]
        for d in resumo["documentos"]:
            data = d["data"].strftime("%d/%m/%Y") if d["data"] else "sem data"
            linhas.append(f"- **{d['documento']}** — {d['tipo']} {d['numero']} ({data}). {d['objeto'][:150]}")
        linhas += ["", "## Dossiês e continuação", ""]
        for g in resumo["dossies"]:
            linhas.append(f"- **{g['dossie']}** ({g['identificacao']}): " + " → ".join(g["documentos"]))
        if resumo["lacunas"]:
            linhas += ["", "**O que falta / próximos documentos esperados:**", ""]
            linhas += [f"- {l['dossie']}: {l['lacuna']}" for l in resumo["lacunas"]]
        linhas.append("")
    lgpd = resumo["dados_pessoais_lgpd"]
    if lgpd["risco_por_arquivo"]:
        linhas += ["## Dados pessoais e sensíveis (LGPD)", ""]
        if lgpd["ocorrencias_por_tipo"]:
            linhas.append("- Encontrados: " + ", ".join(f"{t} ({n})" for t, n in lgpd["ocorrencias_por_tipo"].items()))
        for r in lgpd["risco_por_arquivo"]:
            if r["risco"] != "BAIXO":
                linhas.append(f"- {r['arquivo']}: risco **{r['risco']}**")
        linhas.append("- Versões anonimizadas disponíveis no pacote ZIP.")
        linhas.append("")
    if not resumo["tabelas"]:
        return "\n".join(linhas)
    linhas += ["## Bases recebidas", ""]
    for t in resumo["tabelas"]:
        linhas.append(
            f"- **{t['nome']}**: {t['linhas_originais']} linhas → {t['linhas_finais']} após limpeza "
            f"({t['duplicadas_removidas']} duplicatas), {t['colunas']} colunas, {t['problemas']} problema(s)."
        )
    linhas += ["", "## Qualidade dos dados", ""]
    if resumo["problemas_por_tipo"]:
        for p in resumo["problemas_por_tipo"]:
            linhas.append(f"- {p['tabela']}: {p['qtd']} × {p['problema']}")
    else:
        linhas.append("- Nenhum problema de formato encontrado.")
    s = resumo["sincronizacao"]
    linhas += ["", "## Cruzamento", ""]
    linhas += [f"- {o}" for o in s["observacoes"]]
    for c in s["cobertura"]:
        linhas.append(
            f"- {c['tabela']}: {c['chaves_distintas']} registros ({c['pct_do_total']}% do total), "
            f"{c['exclusivas_desta_tabela']} só nesta base."
        )
    return "\n".join(linhas)


def _nome_aba(nome: str, usados: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", nome)[:28] or "aba"
    candidato, n = base, 2
    while candidato.lower() in usados:
        candidato = f"{base[:26]}_{n}"
        n += 1
    usados.add(candidato.lower())
    return candidato


def gerar_excel(resultado: ResultadoAgente) -> bytes:
    """Planilha final: Resumo, Consolidado, Cobertura, Divergências, Problemas, Dicionário e tabelas limpas."""
    saida = io.BytesIO()
    usados: set[str] = set()
    sinc = resultado.sincronizacao
    with pd.ExcelWriter(saida, engine="openpyxl", datetime_format="DD/MM/YYYY", date_format="DD/MM/YYYY") as escritor:
        resumo = pd.DataFrame({"parecer": resultado.parecer.splitlines()})
        resumo.to_excel(escritor, sheet_name=_nome_aba("Resumo", usados), index=False)
        abas = [
            ("Documentos", resultado.fichas),
            ("Dossies", resultado.dossies),
            ("Continuacao_vinculos", resultado.vinculos),
            ("Lacunas", resultado.lacunas),
            ("Dados_sensiveis_LGPD", resultado.sensiveis),
            ("Risco_LGPD", resultado.risco),
            ("Docs_x_Tabelas", resultado.cruzamento_docs_dados),
            ("Consolidado", sinc.consolidado),
            ("Cobertura", sinc.cobertura),
            ("Divergencias", sinc.divergencias),
            ("Problemas", resultado.problemas),
            ("Dicionario", resultado.dicionario),
        ]
        abas += [(f"Limpo_{t.nome}", t.dados) for t in resultado.tabelas]
        for nome, df in abas:
            if df is None or df.empty:
                continue
            df.to_excel(escritor, sheet_name=_nome_aba(nome, usados), index=False)
        for planilha in escritor.book.worksheets:
            planilha.freeze_panes = "A2"
            for coluna in planilha.columns:
                largura = max(len(str(c.value or "")) for c in list(coluna)[:200])
                planilha.column_dimensions[coluna[0].column_letter].width = min(max(10, largura + 2), 60)
    return saida.getvalue()


def gerar_pacote_zip(resultado: ResultadoAgente) -> bytes:
    """ZIP com parecer, CSVs limpos e versões ANONIMIZADAS de documentos e tabelas (prontas para publicar)."""
    import zipfile

    def csv(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False, sep=";").encode("utf-8-sig")

    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("parecer.md", resultado.parecer)
        if len(resultado.sincronizacao.consolidado):
            z.writestr("consolidado.csv", csv(resultado.sincronizacao.consolidado))
        for t in resultado.tabelas:
            z.writestr(f"limpo/{t.nome}.csv", csv(t.dados))
        for nome, df in resultado.tabelas_anonimizadas.items():
            z.writestr(f"anonimizado/tabelas/{nome}.csv", csv(df))
        for nome, texto in resultado.documentos_anonimizados.items():
            z.writestr(f"anonimizado/documentos/{nome}.txt", texto)
        for doc in resultado.documentos:
            z.writestr(f"texto_extraido/{doc.nome}.txt", doc.texto)
    return pacote.getvalue()
