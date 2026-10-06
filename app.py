"""Interface web: suba os arquivos e o agente organiza e sincroniza os dados.

Executar:  streamlit run app.py
"""

import io
import zipfile

import streamlit as st

from agente import executar, gerar_excel
from agente.agente_ia import ia_disponivel

st.set_page_config(page_title="Agente de Dados do Governo", page_icon="🏛️", layout="wide")
st.title("🏛️ Agente de Organização e Sincronização de Dados")
st.caption(
    "Suba planilhas e arquivos de bases governamentais (CSV, Excel, JSON). O agente padroniza colunas, "
    "valida CPF/CNPJ/CEP/UF, converte datas e valores em R$, remove duplicatas e cruza as bases."
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
    "Arquivos de dados",
    type=["csv", "txt", "xlsx", "xlsm", "xls", "json"],
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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tabelas", len(resultado.tabelas))
    c2.metric("Registros consolidados", len(sinc.consolidado))
    c3.metric("Problemas encontrados", len(resultado.problemas))
    c4.metric("Divergências entre bases", len(sinc.divergencias))

    st.download_button(
        "⬇️ Baixar planilha organizada (Excel)",
        data=gerar_excel(resultado),
        file_name="resultado_organizado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("parecer.md", resultado.parecer)
        if len(sinc.consolidado):
            z.writestr("consolidado.csv", sinc.consolidado.to_csv(index=False, sep=";").encode("utf-8-sig"))
        for t in resultado.tabelas:
            z.writestr(f"limpo_{t.nome}.csv", t.dados.to_csv(index=False, sep=";").encode("utf-8-sig"))
    st.download_button("⬇️ Baixar CSVs + parecer (ZIP)", data=pacote.getvalue(), file_name="resultado.zip")

    abas = st.tabs(["Parecer", "Consolidado", "Cobertura", "Divergências", "Problemas", "Dicionário", "Tabelas limpas"])
    with abas[0]:
        st.markdown(resultado.parecer)
    with abas[1]:
        st.dataframe(sinc.consolidado, width="stretch")
    with abas[2]:
        st.dataframe(sinc.cobertura, width="stretch")
    with abas[3]:
        st.dataframe(sinc.divergencias, width="stretch")
    with abas[4]:
        st.dataframe(resultado.problemas, width="stretch")
    with abas[5]:
        st.dataframe(resultado.dicionario, width="stretch")
    with abas[6]:
        for t in resultado.tabelas:
            with st.expander(f"{t.nome} — {len(t.dados)} linhas"):
                if t.nome in resultado.descricoes:
                    st.caption(resultado.descricoes[t.nome])
                st.dataframe(t.dados, width="stretch")
