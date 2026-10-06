"""Orquestra o agente: ler → entender → limpar → sincronizar → relatar."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import agente_ia
from .leitor import ler_arquivo
from .normalizador import ResultadoTabela, normalizar_tabela, perfil_tabela
from .sincronizador import ResultadoSincronizacao, sincronizar


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

    # 1. Leitura
    brutas: dict[str, pd.DataFrame] = {}
    for nome, conteudo in arquivos:
        try:
            lidas = ler_arquivo(nome, conteudo)
        except Exception as erro:  # arquivo corrompido não deve derrubar os demais
            registrar(f"⚠️ Não foi possível ler '{nome}': {erro}")
            continue
        for tabela, df in lidas.items():
            chave = tabela
            contador = 2
            while chave in brutas:
                chave = f"{tabela}_{contador}"
                contador += 1
            brutas[chave] = df
            registrar(f"📥 {chave}: {len(df)} linhas × {len(df.columns)} colunas")
    if not brutas:
        raise ValueError("Nenhum arquivo pôde ser lido.")

    # 2. Entendimento das colunas pela IA (opcional)
    mapeamento: dict = {}
    if usar_ia:
        if agente_ia.ia_disponivel():
            registrar("🤖 IA analisando o significado das colunas...")
            try:
                mapeamento = agente_ia.aplicar_mapeamento(agente_ia.mapear_colunas(brutas))
                registrar("🤖 Colunas mapeadas para nomes padronizados.")
            except Exception as erro:
                registrar(f"⚠️ IA indisponível ({erro}); seguindo só com regras automáticas.")
        else:
            registrar("⚠️ ANTHROPIC_API_KEY não configurada; seguindo só com regras automáticas.")

    # 3. Limpeza e padronização
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

    # 4. Sincronização
    sinc = sincronizar(tabelas, chave_forcada=chave_forcada)
    for obs in sinc.observacoes:
        registrar(f"🔗 {obs}")

    dicionario = pd.concat([perfil_tabela(t) for t in tabelas], ignore_index=True)
    problemas = pd.DataFrame([p for t in tabelas for p in t.problemas])
    if problemas.empty:
        problemas = pd.DataFrame(columns=["tabela", "linha", "coluna", "valor_original", "problema"])

    resumo = montar_resumo(tabelas, sinc, problemas, descricoes)
    parecer = parecer_automatico(resumo)
    if usar_ia and agente_ia.ia_disponivel():
        registrar("🤖 IA redigindo o parecer...")
        try:
            parecer = agente_ia.redigir_parecer(resumo)
        except Exception as erro:
            registrar(f"⚠️ Parecer da IA falhou ({erro}); usando parecer automático.")

    registrar("✅ Processamento concluído.")
    return ResultadoAgente(tabelas, sinc, dicionario, problemas, resumo, parecer, descricoes, log)


def montar_resumo(tabelas, sinc: ResultadoSincronizacao, problemas: pd.DataFrame, descricoes) -> dict:
    """Estatísticas agregadas — sem dados pessoais — usadas no parecer e na aba Resumo."""
    por_tipo = problemas.groupby(["tabela", "problema"]).size().reset_index(name="qtd") if len(problemas) else None
    return {
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
    linhas = ["# Parecer do processamento", "", "## Bases recebidas", ""]
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
