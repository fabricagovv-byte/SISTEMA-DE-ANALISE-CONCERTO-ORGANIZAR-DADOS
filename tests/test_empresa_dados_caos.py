"""Aceitação com 'empresa_dados_caos.zip' (26 arquivos, 12 pastas, 14 formatos).

Os números esperados vêm do gabarito do autor do pacote; o agente nunca lê o gabarito.
"""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from agente import executar, gerar_excel, gerar_pacote_zip

ZIP = Path(__file__).parent / "fixtures" / "empresa_dados_caos.zip"


@pytest.fixture(scope="module")
def r():
    return executar([(ZIP.name, ZIP.read_bytes())])


def test_inventario(r):
    inv = r.inventario.set_index("caminho")
    for dup in ("Desktop - Copia/clientes_master - Copia.csv", "Financeiro/2024/NAO USAR/vendas_2024_consolidado (1).xlsx",
                "_lixeira/settings.yaml.bak"):
        assert "cópia idêntica" in inv.loc[dup, "situacao"]
    for lixo in ("_lixeira/Thumbs.db", "_lixeira/.DS_Store", "_lixeira/desktop.ini", "_lixeira/~$folha_e_cadastro.xlsx",
                 "Desktop - Copia/Nova pasta/Novo Documento de Texto.txt"):
        assert inv.loc[lixo, "situacao"].startswith("REMOVER")
    for segredo in ("TI/configs/id_rsa", "TI/configs/settings.yaml"):
        assert inv.loc[segredo, "categoria"] == "segredo"
    assert "Desktop - Copia/Nova pasta/arquivos.zip/leads_feira.csv" in inv.index
    for f in ("customers_export_2021.csv", "fornecedores.xml", "sistema_estoque.db", "backup_crm_2022.sql", "gateway_pagamentos.jsonl"):
        assert any(f in c for c in r.fontes_lidas), f


def test_clientes(r):
    c = r.entidades.pessoas.set_index("id_cliente")
    assert len(c) == 40 and (c["ativo"] == "sim").sum() == 39 and c.loc["6", "ativo"] == "não"
    assert c.loc["37", "cpf"] != c.loc["38", "cpf"]                       # homônimos separados
    assert (c["nome"] == "José Carlos Andrade").sum() == 1                # 3 variações unidas
    assert c.loc["1", "cpf"] == "784.864.887-70"                          # CPF trocado não sobrescreve
    assert c.loc["12", "cpf"] == "425.247.764-68" and c.loc["25", "cpf"] == "453.557.787-04"
    assert c.loc["3", "email"].endswith("@novoexemplo.com")
    conf = r.entidades.conflitos
    assert conf["motivo"].str.contains("CPF trocado").any()
    assert "leads_feira" in r.entidades.outros_cadastros                   # leads não viram clientes


def test_vendas_e_conciliacao(r):
    d = r.dominios
    vendas = d["vendas"]
    assert len(vendas) == 300 and abs(vendas["total_brl"].sum() - 338715.27) < 0.01
    assert int(vendas["alertas"].str.contains("AMBÍGUO").sum()) == 3
    sep = d["vendas_excluidas"]
    assert {"PV99999", "PV77777"} <= set(sep["pedido"])
    assert sep["pedido"].str.startswith("WEB-").sum() == 6
    co = d["conciliacao"]
    assert sorted(co[co["situacao"].str.startswith("valor divergente")]["pedido"]) == [
        "PV00014", "PV00017", "PV00057", "PV00112", "PV00148"]


def test_produtos_fornecedores(r):
    p = r.dominios["produtos"].set_index("sku")
    assert len(p) == 25 and p.loc["SKU-1002", "preco_brl"] == 341.95 and p.loc["SKU-1002", "peso_kg"] == 4.004
    assert sorted(p[p["saldo_estoque"] < 0].index) == [
        "SKU-1002", "SKU-1003", "SKU-1004", "SKU-1009", "SKU-1011", "SKU-1014",
        "SKU-1015", "SKU-1016", "SKU-1023", "SKU-1024", "SKU-1025"]
    f = r.dominios["fornecedores"].set_index("codigo")
    assert len(f) == 8 and f.loc["F006", "cnpj_valido"] == "não"


def test_sensiveis_e_vazamento(r):
    criticos = set(r.risco.loc[r.risco["risco"] == "CRÍTICO", "arquivo"])
    assert {"sistema_estoque__config", "backup_crm_2022__clientes", "gateway_pagamentos",
            "RE_ FW_ planilha clientes urgente", "app_2025-09"} <= criticos
    excel = pd.read_excel(io.BytesIO(gerar_excel(r)), sheet_name=None, dtype=str)
    with zipfile.ZipFile(io.BytesIO(gerar_pacote_zip(r))) as z:
        textos = [z.read(n).decode("utf-8-sig", "replace") for n in z.namelist()]
    textos += [df.to_csv() for df in excel.values()] + [r.parecer]
    for segredo in ("SenhaFalsa#2025", "SmtpFalsa", "FAKEKEYFAKEKEY", "e10adc3949ba59abbe56e057f20f883e",
                    "4111111111111111", "5555555555554444", "4012888888881881", "cvv 123", "admin/admin",
                    "senha@1", "alergia a amendoim", "grávida"):
        assert not any(segredo in t for t in textos), segredo
