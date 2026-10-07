"""Teste de aceitação com o pacote 'banco_desorganizado.zip' e o gabarito que o acompanha."""

import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from agente import executar, gerar_excel, gerar_pacote_zip
from agente.validadores import cpf_valido

ZIP = Path(__file__).parent / "fixtures" / "banco_desorganizado.zip"
SEGREDOS = [
    "Root@2024", "sk_test_FAKE", "FAKE-SECRET", "admin123", "Fin@nc3", "qwerty", "senha123",
    "4111 1111 1111 1111", "4111111111111111", "5555555555554444", "4012888888881881",
]


@pytest.fixture(scope="module")
def r():
    return executar([(ZIP.name, ZIP.read_bytes())])


@pytest.fixture(scope="module")
def saidas(r):
    excel = pd.read_excel(io.BytesIO(gerar_excel(r)), sheet_name=None, dtype=str)
    with zipfile.ZipFile(io.BytesIO(gerar_pacote_zip(r))) as z:
        arquivos = {n: z.read(n).decode("utf-8-sig") for n in z.namelist()}
    return excel, arquivos


def _situacao(r, caminho):
    return r.inventario.set_index("caminho").loc[caminho]


def test_estrutura_lixo_duplicata_nomes_e_quarentena(r):
    for lixo in ("temp/Thumbs.db", "temp/~$vendas.xlsx", "Documento sem título.txt"):
        assert _situacao(r, lixo)["situacao"].startswith("REMOVER")
    dup = _situacao(r, "backup_old/clientes_FINAL_v2_AGORA_VAI.csv")
    assert "cópia idêntica de 'clientes_FINAL_v2_AGORA_VAI.csv'" in dup["situacao"]
    for segredo in ("senhas_sistema.txt", "temp/.env"):
        assert _situacao(r, segredo)["categoria"] == "segredo"
    problemas = " ".join(r.inventario["problemas_no_nome"])
    for termo in ("Nova pasta", "sem título", "final final", "AGORA_VAI", "cópia"):
        assert termo.lower() in problemas.lower()
    assert _situacao(r, "Nova pasta (2)/vendas 2024 - final final.xlsx")["nome_sugerido"] == "vendas_2024.xlsx"
    assert _situacao(r, "clientes_FINAL_v2_AGORA_VAI.csv")["nome_sugerido"] == "clientes.csv"


def test_leitura_corrige_formatos(r):
    obs = dict(zip(r.inventario["caminho"], r.inventario["observacoes"]))
    assert "latin-1" in obs["clientes_FINAL_v2_AGORA_VAI.csv"] and "';'" in obs["clientes_FINAL_v2_AGORA_VAI.csv"]
    assert "'Nome '" in obs["clientes_FINAL_v2_AGORA_VAI.csv"]
    assert "TOTAL" in obs["clientes_FINAL_v2_AGORA_VAI.csv"] and "vazia" in obs["clientes_FINAL_v2_AGORA_VAI.csv"]
    assert "linha 3" in obs["Nova pasta (2)/vendas 2024 - final final.xlsx"]
    assert "rascunho" in obs["Nova pasta (2)/vendas 2024 - final final.xlsx"]
    assert "3 esquemas" in obs["pedidos.json"]


def test_base_unica_de_20_clientes(r):
    c = r.entidades.pessoas
    assert len(c) == 20
    assert sorted(c["id_cliente"].astype(int)) == list(range(1, 21))
    assert c["cpf"].is_unique and c["email"].dropna().is_unique
    assert r.entidades.resumo["por_fonte"]["clientes_FINAL_v2_AGORA_VAI"]["duplicatas_internas"] == 5
    # CPF: válido, ou explicitamente sinalizado.
    for cpf, status in zip(c["cpf"], c["cpf_status"]):
        assert re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", cpf)
        assert cpf_valido(cpf) or status.startswith("INVÁLIDO")
    # Padronização
    assert all(e == e.lower() and "@@" not in e for e in c["email"].dropna())
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in c["data_nascimento"].dropna())
    assert all(re.fullmatch(r"\+55 \(\d{2}\) \d{4,5}-\d{4}", t) for t in c["telefone"].dropna())
    assert all(n == n.strip() and not n.isupper() and not n.islower() for n in c["nome"])
    assert set(c.loc[c["id_cliente"] == "10", "nome"]) == {"João Pereira"}
    assert "senha" not in " ".join(c.columns).lower()
    # Dado antigo (2019) não sobrescreve o atual
    assert not c["email"].dropna().str.contains("antigo.com").any()
    conflitos = r.entidades.conflitos
    assert conflitos[conflitos["campo"] == "email"]["valores_encontrados"].str.contains("antigo.com").any()


def test_vendas_e_pedidos_ligados_ao_id(r):
    vendas = r.entidades.transacoes["vendas_2024"]
    pedidos = r.entidades.transacoes["pedidos"]
    assert len(vendas) == 40 and vendas["id_cliente"].notna().all()
    assert len(pedidos) == 15 and pedidos["id_cliente"].notna().all()
    assert set(vendas["pago"]) <= {"sim", "não", "não informado"}
    assert set(vendas["moeda"].dropna()) == {"BRL", "USD"}
    assert vendas.loc[vendas["venda_id"] == "V0014", "data"].item() == "2024-07-14"  # 07/14/2024 (MM/DD)
    assert "cartao" not in vendas.columns and vendas["cartao_final"].dropna().str.fullmatch(r"\*{4} \d{4}").all()
    assert set(pedidos["status"]) == {"entregue", "cancelado (a confirmar)"}
    assert pedidos["pedido_id"].tolist() == [str(i) for i in range(15)]


def test_relatorio_aponta_todos_os_dados_sensiveis(r):
    s = r.sensiveis
    def tem(origem, tipo):
        return ((s["origem"] == origem) & (s["tipo"] == tipo)).any()
    assert tem("clientes_FINAL_v2_AGORA_VAI", "Senha em texto puro")
    assert tem("vendas 2024 - final final__Planilha1", "Cartão de crédito")
    assert tem("senhas_sistema.txt", "Chave de API")
    assert tem("senhas_sistema.txt", "Usuário e senha (par)")
    assert tem("senhas_sistema.txt", "Senha/segredo em texto puro")
    assert (s["origem"] == "temp/.env").sum() >= 2
    assert tem("notas", "CPF") and tem("notas", "Telefone")
    assert ((s["origem"] == "pedidos") & s["pagina_ou_coluna"].astype(str).str.contains("cpf")).any()
    risco = dict(zip(r.risco["arquivo"], r.risco["risco"]))
    for critico in ("senhas_sistema.txt", "temp/.env", "clientes_FINAL_v2_AGORA_VAI", "vendas 2024 - final final__Planilha1"):
        assert risco[critico] == "CRÍTICO"


def test_nenhum_segredo_vaza_nas_saidas(saidas):
    excel, arquivos = saidas
    textos = list(arquivos.values()) + [df.to_csv() for df in excel.values()]
    for texto in textos:
        for segredo in SEGREDOS:
            assert segredo not in texto, segredo
    assert not any("senhas_sistema" in n or n.endswith(".env") for n in arquivos)
    assert "dados/clientes.csv" in arquivos and "dados/vendas_2024.csv" in arquivos and "dados/pedidos.csv" in arquivos
