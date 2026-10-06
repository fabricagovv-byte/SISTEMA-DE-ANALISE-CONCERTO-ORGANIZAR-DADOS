# 🏛️ Agente de Organização e Sincronização de Dados do Governo

Você sobe os arquivos (CSV, Excel, JSON) e o agente:

1. **Lê** qualquer formato comum de base pública — CSV com `;` ou `,`, acentos em Latin-1/UTF-8, Excel com várias abas, JSON de APIs (`{"dados": [...]}`).
2. **Entende as colunas** — detecta CPF, CNPJ, CPF/CNPJ misto, datas, valores em R$, UF, CEP e código IBGE. Com IA ligada, o Claude dá nomes padronizados e iguala colunas que significam a mesma coisa em bases diferentes (`NR_CPF`, `CPF do Beneficiário` → `cpf`; `NOME_FAVORECIDO` → `nome`).
3. **Limpa e valida** — valida dígitos de CPF/CNPJ, formata documentos e CEP, converte `R$ 1.234,56` em número, `31/12/2024` em data, "São Paulo" em `SP`, remove linhas duplicadas e lista cada problema com tabela, linha e coluna.
4. **Sincroniza** — escolhe sozinho a melhor chave comum (CPF, CNPJ, código IBGE ou coluna de mesmo nome), cruza todas as bases e mostra:
   - **Consolidado**: um registro por pessoa/empresa/município com os dados de todas as fontes;
   - **Cobertura**: quantos registros cada base tem e quantos só existem nela;
   - **Divergências**: mesmo campo com valores diferentes entre bases (ex.: nome diferente para o mesmo CPF).
5. **Entrega** uma planilha Excel organizada (Resumo, Consolidado, Cobertura, Divergências, Problemas, Dicionário de dados e cada base limpa), um ZIP com CSVs e um **parecer** em texto.

## Como usar

```bash
pip install -r requirements.txt

# Interface web (suba os arquivos pelo navegador)
streamlit run app.py

# Ou pela linha de comando
python -m agente exemplos/beneficiarios_cadunico.csv exemplos/pagamentos_transparencia.xlsx -o resultado.xlsx
```

Opções da linha de comando:

| Opção | O que faz |
|---|---|
| `--ia` | Usa o Claude para entender as colunas e escrever o parecer |
| `--chave cpf` | Força a chave de cruzamento (`cpf`, `cnpj`, `codigo_ibge`, `documento` ou nome de coluna) |
| `-o arquivo.xlsx` | Nome da planilha de saída |

### Ligando a IA (opcional)

```bash
export ANTHROPIC_API_KEY="sua-chave"
```

Sem a chave, tudo funciona com as regras automáticas. Modelo padrão: `claude-opus-5-5` (troque com `AGENTE_MODELO`).

**Privacidade (LGPD):** a IA **nunca recebe a base inteira**. São enviados só os nomes das colunas, até 5 exemplos por coluna com números longos (CPF, CNPJ, NIS, telefone) mascarados como `###`, e, para o parecer, apenas estatísticas agregadas.

## Estrutura

```
agente/
  leitor.py         leitura de CSV/Excel/JSON
  validadores.py    CPF, CNPJ, CEP, UF, datas e valores em R$
  normalizador.py   detecção de tipos, limpeza, duplicatas, dicionário de dados
  sincronizador.py  escolha da chave, cruzamento, cobertura e divergências
  agente_ia.py      integração com o Claude (mapeamento de colunas e parecer)
  pipeline.py       orquestração e geração do Excel
app.py              interface web (Streamlit)
exemplos/           bases fictícias para teste
tests/              testes automáticos (python -m pytest)
```

## Regras do cruzamento

- Linhas sem a chave ficam fora do consolidado (e isso é avisado).
- Se a mesma chave aparece várias vezes numa base (ex.: vários pagamentos para o mesmo CPF), os **valores em R$ são somados**, os demais campos ficam com o primeiro preenchido e a coluna `qtd_registros__<base>` mostra quantas linhas foram agrupadas. As linhas originais continuam nas abas `Limpo_<base>`.
- As colunas do consolidado seguem o padrão `campo__base` para você saber de onde veio cada informação.
