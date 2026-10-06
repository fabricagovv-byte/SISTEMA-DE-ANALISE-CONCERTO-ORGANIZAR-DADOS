from pathlib import Path

import pandas as pd

from agente import executar, gerar_excel
from agente import validadores as v
from agente.agente_ia import _mascarar, aplicar_mapeamento
from agente.leitor import ler_arquivo
from agente.normalizador import normalizar_tabela
from agente.sincronizador import sincronizar

EXEMPLOS = Path(__file__).resolve().parent.parent / "exemplos"


def test_cpf_cnpj():
    assert v.cpf_valido("529.982.247-25")
    assert not v.cpf_valido("529.982.247-24")
    assert not v.cpf_valido("111.111.111-11")
    assert v.cnpj_valido("11.222.333/0001-81")
    assert v.cnpj_valido("00000000000191")  # Banco do Brasil, zeros à esquerda
    assert not v.cnpj_valido("11.222.333/0001-80")
    assert v.formatar_cnpj("191") == "00.000.000/0001-91"


def test_valores_e_datas():
    assert v.converter_valor_monetario("R$ 1.234,56") == 1234.56
    assert v.converter_valor_monetario("1,234.56") == 1234.56
    assert v.converter_valor_monetario("(1.000,00)") == -1000.0
    assert v.converter_valor_monetario("abc") is None
    assert str(v.converter_data("05/03/2024")) == "2024-03-05"
    assert str(v.converter_data("2024-03-05T10:00:00.000")) == "2024-03-05"
    assert v.converter_data("31/02/2024") is None
    assert v.normalizar_uf("São Paulo") == "SP"
    assert v.formatar_cep("1310100") == "01310-100"


def test_csv_latin1_ponto_e_virgula_preserva_zeros():
    conteudo = "CPF;Município\n05298224725;São Paulo\n".encode("latin-1")
    df = ler_arquivo("dados.csv", conteudo)["dados"]
    assert df.loc[0, "CPF"] == "05298224725"
    assert df.loc[0, "Município"] == "São Paulo"


def test_normalizacao_detecta_tipos_e_problemas():
    df = pd.DataFrame({
        "CPF Titular": ["529.982.247-25", "52998224725", "123"],
        "Valor Pago": ["R$ 10,00", "R$ 10,00", "x"],
        "Data": ["01/01/2024", "01/01/2024", "2024-02-01"],
    })
    r = normalizar_tabela("t", df)
    assert r.tipos == {"cpf_titular": "cpf", "valor_pago": "valor", "data": "data"}
    assert r.duplicadas_removidas == 1
    assert {p["problema"] for p in r.problemas} == {"CPF inválido", "Número/valor não reconhecido"}


def test_sincronizacao_por_cpf_com_divergencia():
    a = normalizar_tabela("a", pd.DataFrame({"cpf": ["529.982.247-25", "111.444.777-35"], "nome": ["Ana", "Beto"]}))
    b = normalizar_tabela("b", pd.DataFrame({
        "NR_CPF": ["52998224725", "52998224725"], "nome": ["Ana Maria", "Ana Maria"], "valor": ["10,00", "5,50"],
    }))
    s = sincronizar([a, b])
    assert s.chave == "cpf"
    assert len(s.consolidado) == 2
    linha = s.consolidado[s.consolidado["cpf"] == "529.982.247-25"].iloc[0]
    assert linha["qtd_fontes"] == 2
    assert linha["valor__b"] == 15.5
    assert list(s.divergencias["campo"]) == ["nome"]


def test_pipeline_completo_com_exemplos():
    arquivos = [(p.name, p.read_bytes()) for p in sorted(EXEMPLOS.iterdir())]
    resultado = executar(arquivos)
    assert resultado.sincronizacao.chave == "cpf"
    assert len(resultado.problemas) > 0
    planilha = pd.read_excel(pd.io.common.BytesIO(gerar_excel(resultado)), sheet_name=None)
    assert {"Resumo", "Consolidado", "Problemas", "Dicionario"} <= set(planilha)


def test_ia_mascara_dados_pessoais_e_aplica_mapeamento():
    assert "529" not in _mascarar("529.982.247-25")
    mapa = aplicar_mapeamento({"tabelas": [{
        "tabela": "x", "descricao": "d",
        "colunas": [{"original": "NOME_FAVORECIDO", "nome_padronizado": "nome", "tipo": "texto"}],
    }]})
    assert mapa["x"][0] == {"NOME_FAVORECIDO": "nome"}
    assert mapa["x"][1] == {"nome": "texto"}
