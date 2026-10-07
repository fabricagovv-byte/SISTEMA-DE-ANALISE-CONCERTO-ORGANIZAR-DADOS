"""Camada opcional de IA (Claude) para entender colunas e redigir o parecer.

Só é usada quando há credencial da Anthropic configurada (ex.: ANTHROPIC_API_KEY).
Antes de enviar, CPFs/CNPJs e outros números longos das amostras são mascarados (LGPD):
a IA vê apenas nomes de colunas, tipos e exemplos anonimizados — nunca a base inteira.
"""

from __future__ import annotations

import json
import os
import re

import pandas as pd

from .normalizador import TIPOS

MODELO = os.environ.get("AGENTE_MODELO", "claude-opus-5-5")

SISTEMA = (
    "Você é um analista de dados do setor público brasileiro. Conhece bases como "
    "Portal da Transparência, SIAFI, CadÚnico, RAIS, CNES, IBGE, TSE e dados de convênios. "
    "Responda sempre em português do Brasil."
)

ESQUEMA_MAPEAMENTO = {
    "type": "object",
    "properties": {
        "tabelas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tabela": {"type": "string"},
                    "descricao": {"type": "string"},
                    "colunas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string"},
                                "nome_padronizado": {"type": "string"},
                                "tipo": {"type": "string", "enum": list(TIPOS)},
                            },
                            "required": ["original", "nome_padronizado", "tipo"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["tabela", "descricao", "colunas"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tabelas"],
    "additionalProperties": False,
}


def ia_disponivel() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))


COLUNAS_SECRETAS = re.compile(r"senha|password|passwd|pwd|secret|token|api_?key|cart[aã]o|card", re.IGNORECASE)


def _mascarar(valor: str) -> str:
    texto = str(valor)
    # Sequências com 8+ dígitos (CPF, CNPJ, NIS, telefone, conta, cartão) viram ###.
    texto = re.sub(r"\d[\d.\-/ ]{6,}\d", lambda m: "#" * len(m.group()), texto)
    # E-mails: mantém só o domínio.
    texto = re.sub(r"[\w.+-]+@([\w-]+\.[\w.-]+)", r"***@\1", texto)
    return texto[:60]


def _descrever(tabelas_brutas: dict[str, pd.DataFrame]) -> str:
    partes = []
    for nome, df in tabelas_brutas.items():
        colunas = []
        for coluna in df.columns:
            if COLUNAS_SECRETAS.search(str(coluna)):
                colunas.append({"coluna": str(coluna), "exemplos": ["[oculto: possível senha/cartão]"]})
                continue
            exemplos = [
                _mascarar(x) for x in df[coluna].astype(str).str.strip().replace("", pd.NA).dropna().unique()[:5]
            ]
            colunas.append({"coluna": str(coluna), "exemplos": exemplos})
        partes.append({"tabela": nome, "linhas": len(df), "colunas": colunas})
    return json.dumps(partes, ensure_ascii=False, indent=1)


def _chamar(cliente, mensagem: str, esquema: dict | None = None, esforco: str = "medium") -> str:
    output_config: dict = {"effort": esforco}
    if esquema:
        output_config["format"] = {"type": "json_schema", "schema": esquema}
    with cliente.beta.messages.stream(
        model=MODELO,
        max_tokens=32000,
        system=SISTEMA,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config=output_config,
        messages=[{"role": "user", "content": mensagem}],
    ) as stream:
        resposta = stream.get_final_message()
    if resposta.stop_reason == "refusal":
        raise RuntimeError("A IA recusou a solicitação.")
    if resposta.stop_reason == "max_tokens":
        raise RuntimeError("Resposta da IA cortada (limite de tokens).")
    return "".join(b.text for b in resposta.content if b.type == "text")


def mapear_colunas(tabelas_brutas: dict[str, pd.DataFrame]) -> dict:
    """Pede à IA nomes padronizados e tipos para cada coluna.

    Colunas que representam a mesma informação em tabelas diferentes recebem o mesmo
    nome padronizado, o que permite ao sincronizador cruzá-las.
    """
    import anthropic

    cliente = anthropic.Anthropic()
    mensagem = (
        "Recebi as tabelas abaixo (nomes de colunas e exemplos já anonimizados; '#' são dígitos ocultos).\n"
        "Para CADA coluna de CADA tabela:\n"
        "1. Dê um nome_padronizado em snake_case, sem acentos. Quando colunas de tabelas diferentes "
        "representam a mesma informação (ex.: 'NR_CPF', 'cpf_beneficiario', 'CPF do Favorecido'), use "
        "exatamente o mesmo nome em todas — isso é o que permite cruzar as bases.\n"
        f"2. Classifique o tipo em um de: {', '.join(TIPOS)}. Use 'valor' para dinheiro, 'cpf_cnpj' quando "
        "a coluna mistura pessoas físicas e jurídicas, 'codigo_ibge' para código de município do IBGE, "
        "'texto' para códigos com zero à esquerda (matrícula, processo, empenho).\n"
        "3. Escreva uma descricao curta de cada tabela (o que parece ser e a provável origem).\n"
        "Mantenha o campo 'original' exatamente igual ao nome recebido.\n\n"
        f"{_descrever(tabelas_brutas)}"
    )
    return json.loads(_chamar(cliente, mensagem, ESQUEMA_MAPEAMENTO))


def aplicar_mapeamento(mapeamento: dict) -> dict[str, tuple[dict[str, str], dict[str, str], str]]:
    """Converte a resposta da IA em {tabela: (renomear, tipos_forcados, descricao)}."""
    from .normalizador import padronizar_nome_coluna

    saida = {}
    for item in mapeamento.get("tabelas", []):
        renomear, tipos = {}, {}
        for col in item.get("colunas", []):
            novo = padronizar_nome_coluna(col["nome_padronizado"])
            renomear[col["original"]] = novo
            tipos[novo] = col["tipo"]
        saida[item["tabela"]] = (renomear, tipos, item.get("descricao", ""))
    return saida


def redigir_parecer(resumo: dict) -> str:
    """Gera um parecer executivo em Markdown a partir das estatísticas (sem dados pessoais)."""
    import anthropic

    cliente = anthropic.Anthropic()
    mensagem = (
        "Com base nas estatísticas abaixo de um processamento de bases de dados governamentais, "
        "escreva um parecer técnico em Markdown com: (1) visão geral das bases, (2) qualidade dos dados "
        "e principais problemas, (3) resultado do cruzamento entre as bases, (4) riscos e pontos de "
        "atenção (ex.: CPFs inválidos, registros só numa base, divergências de valores), "
        "(5) recomendações práticas. Seja objetivo e use números.\n\n"
        f"{json.dumps(resumo, ensure_ascii=False, indent=1, default=str)}"
    )
    return _chamar(cliente, mensagem, esforco="medium")


ESQUEMA_DOCUMENTO = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string"},
        "orgao": {"type": "string"},
        "objeto": {"type": "string"},
        "resumo": {"type": "string"},
        "pontos_de_atencao": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tipo", "orgao", "objeto", "resumo", "pontos_de_atencao"],
    "additionalProperties": False,
}

LIMITE_CARACTERES_DOCUMENTO = 400_000


def analisar_documento(nome: str, texto_anonimizado: str, tipos_validos: list[str]) -> dict:
    """Classifica e resume um documento. Recebe o texto JÁ anonimizado (sem CPF, RG, contatos...).

    Documentos acima de LIMITE_CARACTERES_DOCUMENTO são enviados em parte e isso é
    sinalizado no retorno (chave "_truncado").
    """
    import anthropic

    truncado = len(texto_anonimizado) > LIMITE_CARACTERES_DOCUMENTO
    texto = texto_anonimizado[:LIMITE_CARACTERES_DOCUMENTO]
    cliente = anthropic.Anthropic()
    mensagem = (
        f"Documento público '{nome}' (dados pessoais já substituídos por marcadores como [CPF]).\n"
        f"1. tipo: escolha um de {tipos_validos} ou 'Outro'.\n"
        "2. orgao: órgão emissor.\n3. objeto: objeto/assunto em uma frase.\n"
        "4. resumo: 3 a 5 frases com o essencial (partes, valores, prazos, decisões).\n"
        "5. pontos_de_atencao: irregularidades aparentes, prazos vencidos, valores divergentes, "
        "referências a documentos que deveriam existir (aditivos, anexos, empenhos) — lista vazia se nada.\n"
        + ("(Atenção: apenas o início do documento foi enviado por ser muito longo.)\n" if truncado else "")
        + f"\n<documento>\n{texto}\n</documento>"
    )
    resultado = json.loads(_chamar(cliente, mensagem, ESQUEMA_DOCUMENTO, esforco="low"))
    resultado["_truncado"] = truncado
    return resultado
