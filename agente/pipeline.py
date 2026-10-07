"""Orquestra o agente: ler → entender → limpar → proteger → sincronizar → relatar."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import agente_ia
from . import entidades as ent
from . import inventario as inv
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
    inventario: pd.DataFrame = field(default_factory=pd.DataFrame)
    entidades: ent.ResultadoEntidades | None = None
    inconsistencias_docs_base: pd.DataFrame = field(default_factory=pd.DataFrame)


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

    # 0. Inventário: abre ZIPs, separa lixo, duplicatas e arquivos de credenciais.
    itens = inv.analisar(inv.expandir(arquivos))
    for item in itens:
        if item.categoria in {"lixo", "vazio", "duplicata"}:
            registrar(f"🗑️ {item.caminho}: {item.situacao}")
        elif item.categoria == "segredo":
            registrar(f"🚨 {item.caminho}: {item.situacao}")

    # 1. Leitura: planilhas viram tabelas; PDF/Word/HTML/TXT corrido viram documentos.
    brutas: dict[str, pd.DataFrame] = {}
    caminhos: dict[str, str] = {}
    documentos: dict[str, Documento] = {}
    segredos: dict[str, Documento] = {}
    for item in itens:
        if item.categoria not in {"dados", "documento", "segredo"}:
            continue
        nome, conteudo = item.caminho, item.conteudo
        extensao = item.extensao
        try:
            eh_documento = extensao in EXTENSOES_DOCUMENTO or (extensao == ".txt" and parece_texto_corrido(conteudo))
            if item.categoria == "segredo" or eh_documento:
                doc = ler_documento(nome, conteudo)
                doc.texto = v.corrigir_mojibake(doc.texto)
                doc.paginas = [v.corrigir_mojibake(p) for p in doc.paginas]
                if item.categoria == "segredo":
                    doc.nome = _nome_unico(item.caminho, segredos)
                    segredos[doc.nome] = doc
                    continue
                doc.nome = _nome_unico(doc.nome, documentos)
                documentos[doc.nome] = doc
                registrar(f"📄 {doc.nome}: documento com {len(doc.paginas)} página(s), {len(doc.texto)} caracteres")
                for aviso in doc.avisos:
                    registrar(f"⚠️ {doc.nome}: {aviso}")
                    item.observacoes.append(aviso)
                for i, df in enumerate(doc.tabelas, start=1):
                    chave = _nome_unico(f"{doc.nome}__tabela{i}", brutas)
                    brutas[chave] = df
                    caminhos[chave] = item.caminho
                    registrar(f"📥 {chave}: tabela extraída do documento ({len(df)} linhas)")
            else:
                for tabela, df in ler_arquivo(nome, conteudo).items():
                    chave = _nome_unico(tabela, brutas)
                    brutas[chave] = df
                    caminhos[chave] = item.caminho
                    item.observacoes += _descrever_leitura(df.attrs.get("leitura", {}))
                    registrar(f"📥 {chave}: {len(df)} linhas × {len(df.columns)} colunas")
        except Exception as erro:  # arquivo corrompido não deve derrubar os demais
            registrar(f"⚠️ Não foi possível ler '{nome}': {erro}")
            item.observacoes.append(f"erro de leitura: {erro}")
    if not brutas and not documentos and not segredos:
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
    for doc in segredos.values():
        achados += sens.encontrar_em_texto(doc.texto, doc.nome, doc.paginas)
    sensiveis = pd.DataFrame(achados) if achados else pd.DataFrame(columns=COLUNAS_SENSIVEIS + ["_valor"])
    risco = _risco_por_origem(sensiveis, list(brutas) + list(documentos) + list(segredos))
    if len(sensiveis):
        registrar(
            f"🔒 {int((sensiveis['categoria'] != 'empresa').sum())} ocorrência(s) de dados pessoais/sensíveis/credenciais; "
            f"{int((risco['risco'] == 'CRÍTICO').sum())} arquivo(s) CRÍTICO(s), "
            f"{int((risco['risco'] == 'ALTO').sum())} com risco ALTO."
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

    # 6. Base única de pessoas + transações ligadas ao ID (quando há cadastros);
    #    senão, cruzamento genérico das tabelas por chave comum.
    entidades = ent.consolidar(brutas, caminhos) if brutas else None
    if entidades is not None:
        r = entidades.resumo
        registrar(
            f"👥 Base única: {r['registros_de_pessoas_lidos']} registros de pessoas → {r['pessoas_unicas']} pessoas "
            f"({r['cpf_corrigido_por_outra_fonte']} CPF(s) corrigido(s) por outra fonte, "
            f"{r['cpf_invalido_sem_correcao']} inválido(s) sem correção, {r['conflitos']} conflito(s))."
        )
        for nome_t, info in r["transacoes"].items():
            registrar(f"🔗 {nome_t}: {info['vinculadas']}/{info['linhas']} registros ligados ao ID da pessoa.")
        vazio = pd.DataFrame()
        sinc = ResultadoSincronizacao(None, vazio, vazio, vazio, observacoes=[
            "Cruzamento feito pela base única de pessoas (ver abas Base_unica e transações)."
        ])
    else:
        sinc = sincronizar(tabelas, chave_forcada=chave_forcada)
        for obs in sinc.observacoes:
            registrar(f"🔗 {obs}")
    cruzamento = _cruzar_documentos_e_dados(sensiveis, fichas_obj, tabelas)
    inconsistencias = _inconsistencias_docs_base(documentos, entidades)
    if len(inconsistencias):
        registrar(f"⚠️ {len(inconsistencias)} inconsistência(s) entre documentos/anotações e a base única.")
    if len(cruzamento):
        registrar(f"🔗 {len(cruzamento)} CPF/CNPJ citado(s) em documentos também aparece(m) nas tabelas.")

    dicionario = pd.concat([perfil_tabela(t) for t in tabelas], ignore_index=True) if tabelas else pd.DataFrame()
    if len(dicionario):
        marcas = {(a["origem"], a["pagina_ou_coluna"]): f"{a['categoria']}: {a['tipo']}" for a in achados}
        dicionario["dado_pessoal_lgpd"] = [marcas.get((t, c), "") for t, c in zip(dicionario["tabela"], dicionario["coluna"])]
        # Exemplos de colunas pessoais/secretas não podem aparecer no dicionário de dados.
        marcadas = dicionario["dado_pessoal_lgpd"].ne("") & ~dicionario["dado_pessoal_lgpd"].str.startswith("empresa")
        dicionario.loc[marcadas, "exemplos"] = "[oculto: " + dicionario.loc[marcadas, "dado_pessoal_lgpd"] + "]"
    problemas = pd.DataFrame([p for t in tabelas for p in t.problemas])
    if problemas.empty:
        problemas = pd.DataFrame(columns=["tabela", "linha", "coluna", "valor_original", "problema"])

    inventario_df = inv.tabela(itens)
    resumo = montar_resumo(tabelas, sinc, problemas, descricoes, fichas, dossies, lacunas, sensiveis, risco)
    resumo["inventario"] = _resumo_inventario(itens)
    if entidades is not None:
        resumo["base_unica"] = entidades.resumo
        resumo["suposicoes"] = _suposicoes(entidades.correcoes)
    resumo["inconsistencias_docs_base"] = inconsistencias.to_dict("records")
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
        inventario=inventario_df,
        entidades=entidades,
        inconsistencias_docs_base=inconsistencias,
    )


# Alertas que representam uma SUPOSIÇÃO do agente (não uma correção objetiva) — vão para o parecer.
SUPOSICOES = (
    ("moeda não informada", "valor sem moeda indicada: assumido R$ (BRL)"),
    ("não convertido para BRL", "valor em moeda estrangeira mantido sem conversão (não há cotação nos arquivos)"),
    ("data ambígua: assumido DD/MM", "data ambígua (ex.: 02/10): assumido DD/MM, padrão brasileiro"),
    ("data ambígua: assumido MM/DD", "data ambígua (ex.: 07/04): assumido MM/DD, porque a maioria da coluna é americana"),
    ("formato americano MM/DD", "data em formato americano (MM/DD) convertida"),
    ("inferido", "telefone sem DDD: DDD inferido (o mais comum na base)"),
    ("não-ASCII", "e-mail com acento aceito, mas pode ser rejeitado por outros sistemas"),
)


def _suposicoes(correcoes: pd.DataFrame) -> list[dict]:
    if correcoes is None or correcoes.empty:
        return []
    saida = []
    for trecho, descricao in SUPOSICOES:
        casos = correcoes[correcoes["alerta"].astype(str).str.contains(trecho, regex=False)]
        if len(casos):
            por_tabela = casos.groupby("tabela").size().to_dict()
            saida.append({"suposicao": descricao, "quantidade": len(casos),
                          "onde": ", ".join(f"{t} ({n})" for t, n in por_tabela.items())})
    return saida


CPF_TESTE = {"12345678909", "01234567890", "98765432100"}


def _inconsistencias_docs_base(documentos: dict, entidades) -> pd.DataFrame:
    """Pessoa da base citada num documento/anotação com CPF ou telefone diferente do cadastro."""
    if entidades is None or not documentos:
        return pd.DataFrame()
    pessoas = entidades.pessoas
    por_nome = {v.chave_nome(n): r for n, r in zip(pessoas["nome"], pessoas.to_dict("records")) if n}
    por_cpf = {v.somente_digitos(r["cpf"]): r for r in pessoas.to_dict("records") if r.get("cpf")}
    cpf_re = re.compile(r"(?<![\d./-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d./-])")
    tel_re = re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)")
    linhas_saida = []
    for doc in documentos.values():
        for numero, linha in enumerate(doc.texto.splitlines(), start=1):
            chave_linha = f" {v.chave_nome(linha)} "
            citadas = [r for k, r in por_nome.items() if f" {k} " in chave_linha]
            cpfs = [v.somente_digitos(c) for c in cpf_re.findall(linha)]
            telefones = [t for t in tel_re.findall(linha) if v.somente_digitos(t) not in cpfs]
            for cpf in cpfs:
                if cpf in CPF_TESTE or len(set(cpf)) == 1:
                    linhas_saida.append({"documento": doc.nome, "linha": numero, "pessoa": ", ".join(r["nome"] for r in citadas),
                                         "id_cliente": ", ".join(str(r["id_cliente"]) for r in citadas), "campo": "cpf",
                                         "valor_no_documento": sens.mascarar("CPF", cpf), "valor_na_base": "",
                                         "situacao": "CPF de teste/sequencial (dígitos em sequência) — provavelmente fictício"})
                dono = por_cpf.get(cpf)
                if dono is not None and citadas and all(dono["id_cliente"] != r["id_cliente"] for r in citadas):
                    linhas_saida.append({"documento": doc.nome, "linha": numero, "pessoa": ", ".join(r["nome"] for r in citadas),
                                         "id_cliente": ", ".join(str(r["id_cliente"]) for r in citadas), "campo": "cpf",
                                         "valor_no_documento": sens.mascarar("CPF", cpf), "valor_na_base": sens.mascarar("CPF", dono["cpf"]),
                                         "situacao": f"CPF pertence a outra pessoa da base ({dono['nome']}, id {dono['id_cliente']})"})
            for r in citadas:
                cpf_base = v.somente_digitos(r.get("cpf") or "")
                for cpf in cpfs:
                    if cpf_base and cpf != cpf_base and por_cpf.get(cpf) is None:
                        linhas_saida.append({"documento": doc.nome, "linha": numero, "pessoa": r["nome"], "id_cliente": r["id_cliente"],
                                             "campo": "cpf", "valor_no_documento": sens.mascarar("CPF", cpf),
                                             "valor_na_base": sens.mascarar("CPF", cpf_base),
                                             "situacao": "CPF citado no documento é diferente do CPF da pessoa na base"
                                                         + ("" if v.cpf_valido(cpf) else " (e é inválido)")})
                tel_base = v.somente_digitos(r.get("telefone") or "")[-9:]
                for tel in telefones:
                    if tel_base and v.somente_digitos(tel)[-9:] != tel_base:
                        linhas_saida.append({"documento": doc.nome, "linha": numero, "pessoa": r["nome"], "id_cliente": r["id_cliente"],
                                             "campo": "telefone", "valor_no_documento": sens.mascarar("Telefone", tel),
                                             "valor_na_base": sens.mascarar("Telefone", r["telefone"]),
                                             "situacao": "telefone citado no documento não é o telefone cadastrado"})
    return pd.DataFrame(linhas_saida).drop_duplicates() if linhas_saida else pd.DataFrame()


def _descrever_leitura(info: dict) -> list[str]:
    obs = []
    if info.get("formato") in {"CSV", "JSON"}:
        obs.append(f"{info['formato']} em {info.get('codificacao')}" + (f", separador '{info['separador']}'" if info.get("separador") else "")
                   + " → saída em UTF-8")
    if info.get("cabecalho_na_linha"):
        obs.append(f"cabeçalho na linha {info['cabecalho_na_linha']} (título ignorado: '{info.get('titulo_ignorado', '')}')")
    if info.get("cabecalhos_corrigidos"):
        obs.append("cabeçalho com espaços sobrando: " + ", ".join(repr(c) for c in info["cabecalhos_corrigidos"]))
    if info.get("linhas_vazias_removidas"):
        obs.append(f"{info['linhas_vazias_removidas']} linha(s) vazia(s) removida(s)")
    if info.get("linhas_total_removidas"):
        obs.append(f"{info['linhas_total_removidas']} linha(s) de TOTAL no meio dos dados removida(s)")
    if info.get("abas_descartadas"):
        obs.append("aba(s) descartada(s): " + ", ".join(info["abas_descartadas"]))
    if info.get("esquemas_diferentes", 1) > 1:
        obs.append(f"{info['esquemas_diferentes']} esquemas de chaves diferentes unificados")
    if info.get("ids_tipos_misturados"):
        obs.append("IDs ora número, ora texto (" + ", ".join(f"{k}: {'/'.join(t)}" for k, t in info["ids_tipos_misturados"].items())
                   + ") → padronizados como texto")
    return obs


def _resumo_inventario(itens) -> dict:
    return {
        "arquivos": len(itens),
        "removidos": [f"{i.caminho} — {i.situacao}" for i in itens if i.categoria in {"lixo", "vazio", "duplicata"}],
        "quarentena": [i.caminho for i in itens if i.categoria == "segredo"],
        "nomes_ruins": [
            f"{i.caminho}: {'; '.join(dict.fromkeys(i.problemas_nome))}" + (f" → sugerido '{i.nome_sugerido}'" if i.nome_sugerido else "")
            for i in itens if i.problemas_nome
        ],
        "observacoes_de_leitura": {i.caminho: i.observacoes for i in itens if i.observacoes},
    }


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
                "CRÍTICO": "Retirar o arquivo da pasta compartilhada; TROCAR as senhas/chaves expostas; guardar segredos "
                           "em cofre de senhas; nunca guardar número completo de cartão (PCI-DSS).",
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
            "criticos": [
                f"{r['origem']} — {r['tipo']}" + (f" (coluna '{r['pagina_ou_coluna']}', {r['contexto']})"
                                                  if isinstance(r["pagina_ou_coluna"], str) else f": {r['contexto']}")
                for r in sensiveis.to_dict("records") if r["categoria"] in {"credencial", "financeiro", "infraestrutura"}
            ] if len(sensiveis) else [],
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
    linhas += _parecer_inventario_e_base(resumo)
    if resumo["documentos"]:
        linhas += ["## Documentos recebidos", ""]
        for d in resumo["documentos"]:
            data = d["data"].strftime("%d/%m/%Y") if d["data"] else "sem data"
            linhas.append(f"- **{d['documento']}** — {d['tipo']} {d['numero']} ({data}). {d['objeto'][:150]}")
        dossies_reais = [g for g in resumo["dossies"] if len(g["documentos"]) > 1]
        if dossies_reais or resumo["lacunas"]:
            linhas += ["", "## Dossiês e continuação", ""]
        for g in dossies_reais:
            linhas.append(f"- **{g['dossie']}** ({g['identificacao']}): " + " → ".join(g["documentos"]))
        if resumo["lacunas"]:
            linhas += ["", "**O que falta / próximos documentos esperados:**", ""]
            linhas += [f"- {l['dossie']}: {l['lacuna']}" for l in resumo["lacunas"]]
        linhas.append("")
    lgpd = resumo["dados_pessoais_lgpd"]
    if lgpd["risco_por_arquivo"]:
        linhas += ["## Dados pessoais e sensíveis (LGPD)", ""]
        if lgpd.get("criticos"):
            linhas.append("**🚨 Achados críticos (credenciais, senhas e cartões expostos):**")
            linhas += [f"- {c}" for c in lgpd["criticos"]]
            linhas.append("")
        if lgpd["ocorrencias_por_tipo"]:
            linhas.append("- Encontrados: " + ", ".join(f"{t} ({n})" for t, n in lgpd["ocorrencias_por_tipo"].items()))
        for r in lgpd["risco_por_arquivo"]:
            if r["risco"] != "BAIXO":
                linhas.append(f"- {r['arquivo']}: risco **{r['risco']}**")
        linhas.append("- Versões anonimizadas disponíveis no pacote ZIP.")
        linhas.append("")
    if not resumo["tabelas"]:
        return "\n".join(linhas)
    linhas += ["## Bases recebidas (limpeza linha a linha, antes da unificação)", ""]
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


def _parecer_inventario_e_base(resumo: dict) -> list[str]:
    linhas = []
    inv_ = resumo.get("inventario") or {}
    if inv_:
        linhas += ["## Estrutura de pastas e arquivos", "", f"- {inv_['arquivos']} arquivo(s) recebido(s)."]
        if inv_["removidos"]:
            linhas.append("- **Removidos da saída (lixo, vazios, duplicatas):**")
            linhas += [f"  - {x}" for x in inv_["removidos"]]
        if inv_["quarentena"]:
            linhas.append("- **🚨 Em quarentena (credenciais — NÃO copiados para a saída):** " + ", ".join(inv_["quarentena"]))
        if inv_["nomes_ruins"]:
            linhas.append("- **Nomes de arquivo/pasta problemáticos:**")
            linhas += [f"  - {x}" for x in inv_["nomes_ruins"]]
        if inv_["observacoes_de_leitura"]:
            linhas.append("- **Problemas de formato corrigidos na leitura:**")
            linhas += [f"  - {k}: {'; '.join(o)}" for k, o in inv_["observacoes_de_leitura"].items()]
        linhas.append("")
    base = resumo.get("base_unica")
    if resumo.get("suposicoes") or resumo.get("inconsistencias_docs_base") or (base and base.get("emails_com_acento")):
        linhas += ["## ⚠️ Alertas e suposições para conferir", ""]
        for s_ in resumo.get("suposicoes", []):
            linhas.append(f"- **{s_['quantidade']}× {s_['suposicao']}** — {s_['onde']}")
        if base and base.get("emails_com_acento"):
            linhas.append(f"- E-mails com acento na base única: {', '.join(base['emails_com_acento'])} — "
                          "tecnicamente válidos, mas muitos sistemas rejeitam; confirmar com o titular.")
        for i in resumo.get("inconsistencias_docs_base", []):
            quem = f"{i['pessoa']} (id {i['id_cliente']})" if i["pessoa"] else "pessoa não identificada"
            base_txt = f"; na base: {i['valor_na_base']}" if i["valor_na_base"] else ""
            linhas.append(f"- **{i['documento']}, linha {i['linha']}** — {quem}: {i['campo']} {i['valor_no_documento']}"
                          f"{base_txt} → {i['situacao']}")
        linhas.append("")
    if base:
        linhas += ["## Base única de pessoas", ""]
        linhas.append(f"- {base['registros_de_pessoas_lidos']} registros lidos em {len(base['por_fonte'])} fonte(s) → "
                      f"**{base['pessoas_unicas']} pessoas únicas**, sem duplicatas.")
        for nome, f in base["por_fonte"].items():
            linhas.append(f"  - {nome}: {f['linhas']} linhas, {f['pessoas_distintas']} pessoas"
                          f" ({f['duplicatas_internas']} duplicata(s) interna(s)) — confiabilidade {f['prioridade']}: {f['avaliacao']}")
        linhas.append(f"- CPF: {base['cpf_corrigido_por_outra_fonte']} corrigido(s) com o valor válido de outra fonte; "
                      f"{base['cpf_invalido_sem_correcao']} continua(m) inválido(s) — conferir com o titular.")
        linhas.append(f"- {base['conflitos']} divergência(s) entre fontes resolvida(s) e listada(s) na aba Conflitos "
                      "(valores de backups antigos foram descartados).")
        if base.get("senhas_em_texto_puro"):
            linhas.append(f"- 🚨 {base['senhas_em_texto_puro']} registro(s) com **senha em texto puro** — coluna removida de todas as saídas.")
        for nome, t in base["transacoes"].items():
            linhas.append(f"- **{nome}**: {t['vinculadas']} de {t['linhas']} registros ligados ao ID da pessoa.")
        if base.get("formatos_encontrados"):
            linhas += ["", "**Formatos encontrados na origem (todos padronizados na saída):**", ""]
            padrao = {"cpf": "000.000.000-00 com dígito validado", "nascimento": "AAAA-MM-DD (ISO)",
                      "data": "AAAA-MM-DD (ISO)", "telefone": "+55 (DD) NNNNN-NNNN", "email": "minúsculas, validado",
                      "nome": "Nome Próprio", "valor": "número + coluna moeda", "pago": "sim / não / não informado",
                      "cliente": "id_cliente", "status": "minúsculas; '?' vira '(a confirmar)'"}
            for fonte, campos in base["formatos_encontrados"].items():
                for campo, contagem in campos.items():
                    if len(contagem) > 1:
                        detalhe = ", ".join(f"{k} ({n})" for k, n in contagem.items())
                        linhas.append(f"- {fonte} · **{campo}**: {len(contagem)} formatos — {detalhe} → {padrao.get(campo, '')}")
        linhas.append("")
    return linhas


def _sem_segredos(df: pd.DataFrame, origem: str, sensiveis: pd.DataFrame) -> pd.DataFrame:
    """Remove colunas com senha/cartão antes de qualquer exportação."""
    if not len(sensiveis):
        return df
    ruins = sensiveis[(sensiveis["origem"] == origem) & sensiveis["categoria"].isin(["credencial", "financeiro"])]
    return df.drop(columns=[c for c in ruins["pagina_ou_coluna"] if c in df.columns])


def _nome_base_pessoas(resultado: ResultadoAgente) -> str:
    fontes = resultado.entidades.fontes if resultado.entidades is not None else pd.DataFrame()
    nomes = " ".join(fontes[fontes["tipo"] == "pessoas"]["tabela"]).lower() if len(fontes) else ""
    for chave, nome in (("client", "clientes"), ("benefici", "beneficiarios"), ("servidor", "servidores"),
                        ("aluno", "alunos"), ("paciente", "pacientes"), ("fornecedor", "fornecedores")):
        if chave in nomes:
            return nome
    return "pessoas"


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
        abas = [("Inventario_arquivos", resultado.inventario)]
        e = resultado.entidades
        if e is not None:
            abas.append((f"Base_unica_{_nome_base_pessoas(resultado)}", e.pessoas))
            abas += [(nome.capitalize(), df) for nome, df in e.transacoes.items()]
            abas += [("Conflitos_entre_fontes", e.conflitos), ("Correcoes_aplicadas", e.correcoes),
                     ("Nao_vinculados", e.nao_vinculados), ("Fontes_avaliadas", e.fontes),
                     ("Docs_x_Base_inconsistencias", resultado.inconsistencias_docs_base)]
        abas += [
            ("Risco_LGPD", resultado.risco),
            ("Dados_sensiveis_LGPD", resultado.sensiveis),
            ("Documentos", resultado.fichas),
            ("Dossies", resultado.dossies),
            ("Continuacao_vinculos", resultado.vinculos),
            ("Lacunas", resultado.lacunas),
            ("Docs_x_Tabelas", resultado.cruzamento_docs_dados),
            ("Consolidado", sinc.consolidado),
            ("Cobertura", sinc.cobertura),
            ("Divergencias", sinc.divergencias),
            ("Problemas", resultado.problemas),
            ("Dicionario", resultado.dicionario),
        ]
        abas += [(f"Limpo_{t.nome}", _sem_segredos(t.dados, t.nome, resultado.sensiveis)) for t in resultado.tabelas]
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
    """ZIP organizado, em UTF-8, sem lixo, sem duplicatas e sem credenciais:

    dados/            base única de pessoas e transações ligadas ao ID
    relatorios/       parecer, inventário, conflitos, correções, dados sensíveis, risco
    anonimizado/      versões prontas para publicação (documentos e tabelas)
    limpo_por_fonte/  cada tabela de origem padronizada (sem colunas de senha/cartão)
    texto_extraido/   texto dos documentos sem dados pessoais (risco BAIXO)
    """
    import zipfile

    def csv(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False, sep=";").encode("utf-8-sig")

    pacote = io.BytesIO()
    risco = dict(zip(resultado.risco.get("arquivo", []), resultado.risco.get("risco", [])))
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEIA-ME.md", gerar_pacote_zip.__doc__.split("\n", 2)[2].replace("    ", ""))
        z.writestr("relatorios/parecer.md", resultado.parecer)
        if len(resultado.inventario):
            z.writestr("relatorios/inventario_arquivos.csv", csv(resultado.inventario))
        e = resultado.entidades
        if e is not None:
            z.writestr(f"dados/{_nome_base_pessoas(resultado)}.csv", csv(e.pessoas))
            for nome, df in e.transacoes.items():
                z.writestr(f"dados/{nome}.csv", csv(df))
            for nome, df in (("conflitos_entre_fontes", e.conflitos), ("correcoes_aplicadas", e.correcoes),
                             ("inconsistencias_documentos_x_base", resultado.inconsistencias_docs_base),
                             ("nao_vinculados", e.nao_vinculados), ("fontes_avaliadas", e.fontes)):
                if len(df):
                    z.writestr(f"relatorios/{nome}.csv", csv(df))
        if len(resultado.sensiveis):
            z.writestr("relatorios/dados_sensiveis.csv", csv(resultado.sensiveis))
            z.writestr("relatorios/risco_lgpd.csv", csv(resultado.risco))
        if len(resultado.sincronizacao.consolidado):
            z.writestr("dados/consolidado.csv", csv(resultado.sincronizacao.consolidado))
        for t in resultado.tabelas:
            z.writestr(f"limpo_por_fonte/{t.nome}.csv", csv(_sem_segredos(t.dados, t.nome, resultado.sensiveis)))
        for nome, df in resultado.tabelas_anonimizadas.items():
            z.writestr(f"anonimizado/tabelas/{nome}.csv", csv(df))
        for nome, texto in resultado.documentos_anonimizados.items():
            z.writestr(f"anonimizado/documentos/{nome}.txt", texto)
        for doc in resultado.documentos:
            if risco.get(doc.nome, "BAIXO") == "BAIXO":
                z.writestr(f"texto_extraido/{doc.nome}.txt", doc.texto)
    return pacote.getvalue()
