"""Interface web: suba os arquivos e o agente organiza e sincroniza os dados.

Executar:  streamlit run app.py
"""

import streamlit as st

from agente import executar, gerar_excel, gerar_pacote_zip
from agente.agente_ia import ia_disponivel

st.set_page_config(page_title="Agente de Dados do Governo", page_icon="🏛️", layout="wide")
st.title("🏛️ Agente de Organização e Sincronização de Dados")
st.caption(
    "Suba documentos públicos (PDF, Word, HTML, TXT) e bases de dados (CSV, Excel, JSON). O agente organiza "
    "os documentos em dossiês, encontra a continuação entre eles e o que está faltando, detecta dados pessoais "
    "e sensíveis (LGPD) gerando versões anonimizadas, limpa as tabelas e cruza tudo."
)

with st.sidebar:
    st.header("Configurações")
    tem_ia = ia_disponivel()
    usar_ia = st.toggle(
        "Usar IA (Claude) para entender colunas e redigir parecer",
        value=tem_ia,
        disabled=not tem_ia,
        help="Requer ANTHROPIC_API_KEY. Apenas nomes de colunas e exemplos anonimizados são enviados.",
    )
    if not tem_ia:
        st.info("IA desativada: defina a variável ANTHROPIC_API_KEY para habilitar.")
    chave = st.text_input(
        "Chave de cruzamento (opcional)",
        placeholder="automática — ex.: cpf, cnpj, codigo_ibge, matricula",
    ).strip() or None

arquivos = st.file_uploader(
    "Documentos e arquivos de dados",
    type=["pdf", "docx", "html", "htm", "txt", "md", "rtf", "csv", "xlsx", "xlsm", "xls", "json"],
    accept_multiple_files=True,
)

if arquivos and st.button("▶️ Processar", type="primary"):
    with st.status("Agente trabalhando...", expanded=True) as status:
        try:
            resultado = executar(
                [(a.name, a.getvalue()) for a in arquivos],
                usar_ia=usar_ia,
                chave_forcada=chave,
                progresso=st.write,
            )
        except ValueError as erro:
            status.update(label=str(erro), state="error")
            st.stop()
        status.update(label="Concluído", state="complete", expanded=False)
    st.session_state["resultado"] = resultado

resultado = st.session_state.get("resultado")
if resultado:
    sinc = resultado.sincronizacao
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Documentos", len(resultado.documentos))
    c2.metric("Dossiês", resultado.dossies["dossie"].nunique() if len(resultado.dossies) else 0)
    c3.metric("Lacunas (o que falta)", len(resultado.lacunas))
    c4.metric("Arquivos risco ALTO (LGPD)", int((resultado.risco["risco"] == "ALTO").sum()) if len(resultado.risco) else 0)
    c5.metric("Registros consolidados", len(sinc.consolidado))

    b1, b2 = st.columns(2)
    b1.download_button(
        "⬇️ Baixar planilha organizada (Excel)",
        data=gerar_excel(resultado),
        file_name="resultado_organizado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    b2.download_button(
        "⬇️ Baixar pacote: CSVs + documentos e tabelas ANONIMIZADOS (ZIP)",
        data=gerar_pacote_zip(resultado),
        file_name="resultado.zip",
    )

    abas = st.tabs([
        "Parecer", "Documentos", "Dossiês e continuação", "Dados sensíveis (LGPD)",
        "Consolidado", "Divergências", "Problemas", "Dicionário", "Tabelas limpas",
    ])
    with abas[0]:
        st.markdown(resultado.parecer)
    with abas[1]:
        st.dataframe(resultado.fichas, width="stretch")
        for doc in resultado.documentos:
            with st.expander(f"📄 {doc.nome}"):
                anonimo = st.toggle("Mostrar versão anonimizada", value=True, key=f"anon_{doc.nome}")
                texto = resultado.documentos_anonimizados[doc.nome] if anonimo else doc.texto
                st.text(texto[:20000] + ("\n[...]" if len(texto) > 20000 else ""))
    with abas[2]:
        st.subheader("Dossiês (documentos ligados, em ordem)")
        st.dataframe(resultado.dossies, width="stretch")
        st.subheader("O que falta / próxima continuação esperada")
        st.dataframe(resultado.lacunas, width="stretch")
        st.subheader("Por que os documentos foram ligados")
        st.dataframe(resultado.vinculos, width="stretch")
        if len(resultado.cruzamento_docs_dados):
            st.subheader("CPF/CNPJ dos documentos encontrados nas tabelas")
            st.dataframe(resultado.cruzamento_docs_dados, width="stretch")
    with abas[3]:
        st.subheader("Risco por arquivo")
        st.dataframe(resultado.risco, width="stretch")
        st.subheader("Ocorrências (valores mascarados)")
        st.dataframe(resultado.sensiveis, width="stretch")
    with abas[4]:
        st.dataframe(sinc.consolidado, width="stretch")
        st.caption("Cobertura por base")
        st.dataframe(sinc.cobertura, width="stretch")
    with abas[5]:
        st.dataframe(sinc.divergencias, width="stretch")
    with abas[6]:
        st.dataframe(resultado.problemas, width="stretch")
    with abas[7]:
        st.dataframe(resultado.dicionario, width="stretch")
    with abas[8]:
        for t in resultado.tabelas:
            with st.expander(f"{t.nome} — {len(t.dados)} linhas"):
                if t.nome in resultado.descricoes:
                    st.caption(resultado.descricoes[t.nome])
                st.dataframe(t.dados, width="stretch")
