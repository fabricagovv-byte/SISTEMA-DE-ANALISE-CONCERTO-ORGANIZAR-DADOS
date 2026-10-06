# 🏛️ Agente de Organização e Sincronização de Dados do Governo

Você sobe **documentos públicos** (PDF, Word, HTML, TXT, RTF) e **bases de dados** (CSV, Excel, JSON) — tudo junto — e o agente:

### 📄 Documentos públicos
1. **Lê** PDF (texto e tabelas), DOCX (parágrafos e tabelas), HTML, TXT, MD e RTF. Tabelas encontradas dentro dos documentos entram no cruzamento de dados.
2. **Organiza**: identifica o tipo (edital, contrato, termo aditivo, nota de empenho, ordem bancária, ata, portaria, decreto, ofício, parecer, relatório...), o órgão, número, data, objeto, valores, CNPJs, processos (SEI), contratos, licitações e empenhos citados.
3. **Acha a continuação**: liga os documentos que pertencem à mesma história em **dossiês** — mesmo processo, mesmo contrato, mesma licitação, mesmo empenho, arquivos em partes (`_parte1`, `_parte2`, `volume`, `continuação`) e folhas em sequência (`fls. 10` → `fls. 11`) — e ordena cada dossiê no tempo (edital → homologação → contrato → aditivos → empenho → liquidação → pagamento).
4. **Mostra o que falta**: termo aditivo pulado (tem o 2º mas não o 1º), folhas faltando, parte de arquivo faltando, empenho sem contrato, pagamento sem empenho e o **próximo documento esperado** de cada dossiê.

### 🔒 Dados pessoais e sensíveis (LGPD)
5. **Detecta** em documentos e tabelas: CPF (com dígito verificador), RG, CNH, título de eleitor, PIS/NIS (validado), Cartão SUS (validado), passaporte, nomes de pessoas, e-mail, telefone, endereço, CEP, conta bancária, data de nascimento, placa — e **dados sensíveis** do art. 5º, II: saúde/CID, raça/cor, religião, opinião política, filiação sindical, orientação sexual, biometria, além de indícios de **crianças e adolescentes** (art. 14).
6. **Classifica o risco** de cada arquivo (ALTO / MÉDIO / BAIXO) com recomendação.
7. **Gera versões anonimizadas** prontas para publicação: nos documentos, os dados viram marcadores (`[CPF]`, `[NOME DE PESSOA]`, `[SAÚDE]`...); nas tabelas, CPF fica `***.123.456-**`, nomes viram iniciais, nascimento vira só o ano, e colunas de dados sensíveis são removidas. CNPJ é mantido (dado público).
8. Os relatórios **nunca mostram o valor completo**: tudo aparece mascarado, inclusive o trecho de contexto.

### 📊 Bases de dados
9. **Limpa e valida** tabelas: CPF/CNPJ, datas, valores em R$, UF, CEP, código IBGE, duplicatas.
10. **Sincroniza** as bases por CPF, CNPJ, código IBGE ou coluna comum (consolidado, cobertura e divergências) e cruza com os documentos: mostra quais CPFs/CNPJs citados nos documentos existem nas tabelas.

### 📦 Entregas
- **Planilha Excel**: Resumo, Documentos, Dossiês, Continuação (vínculos), Lacunas, Dados sensíveis, Risco LGPD, Docs × Tabelas, Consolidado, Cobertura, Divergências, Problemas, Dicionário e cada base limpa.
- **ZIP**: parecer, CSVs limpos, texto extraído dos documentos e a pasta `anonimizado/` (documentos e tabelas).
- **Parecer** em texto — automático, ou escrito pelo Claude com a IA ligada.

## Como usar

```bash
pip install -r requirements.txt

# Interface web (suba os arquivos pelo navegador)
streamlit run app.py

# Ou pela linha de comando
python -m agente exemplos/* -o resultado.xlsx     # gera resultado.xlsx e resultado.zip
```

Opções da linha de comando:

| Opção | O que faz |
|---|---|
| `--ia` | Usa o Claude para entender colunas, resumir/classificar documentos e escrever o parecer |
| `--chave cpf` | Força a chave de cruzamento (`cpf`, `cnpj`, `codigo_ibge`, `documento` ou nome de coluna) |
| `-o arquivo.xlsx` | Nome da planilha de saída |

### Ligando a IA (opcional)

```bash
export ANTHROPIC_API_KEY="sua-chave"
```

Sem a chave, tudo funciona com as regras automáticas. Modelo padrão: `claude-opus-5-5` (troque com `AGENTE_MODELO`).

**Privacidade (LGPD):** a IA **nunca recebe dados pessoais em claro**. Das tabelas vão só os nomes das colunas e até 5 exemplos por coluna com números longos mascarados (`###`); dos documentos vai o texto **já anonimizado** (`[CPF]`, `[NOME DE PESSOA]`...); para o parecer, apenas estatísticas agregadas.

**Limitações:** PDFs digitalizados (imagem) precisam de OCR antes — o agente avisa quando encontra um. A detecção de nomes depende de contexto ("Sr.", "representada por", "beneficiária", "Nome:"); nomes soltos no meio do texto podem escapar, então revise documentos de risco ALTO antes de publicar.

## Estrutura

```
agente/
  leitor.py         leitura de CSV/Excel/JSON
  documentos.py     leitura de PDF/DOCX/HTML/TXT/RTF (texto + tabelas)
  organizador_docs.py  classificação, metadados, dossiês, continuação e lacunas
  sensiveis.py      dados pessoais/sensíveis (LGPD), risco e anonimização
  validadores.py    CPF, CNPJ, CEP, UF, datas e valores em R$
  normalizador.py   detecção de tipos, limpeza, duplicatas, dicionário de dados
  sincronizador.py  escolha da chave, cruzamento, cobertura e divergências
  agente_ia.py      integração com o Claude (mapeamento de colunas e parecer)
  pipeline.py       orquestração e geração do Excel
app.py              interface web (Streamlit)
exemplos/           bases e documentos fictícios (edital, contrato, aditivo, empenho, ofício, relatório em partes)
tests/              testes automáticos (python -m pytest)
```

## Regras do cruzamento

- Linhas sem a chave ficam fora do consolidado (e isso é avisado).
- Se a mesma chave aparece várias vezes numa base (ex.: vários pagamentos para o mesmo CPF), os **valores em R$ são somados**, os demais campos ficam com o primeiro preenchido e a coluna `qtd_registros__<base>` mostra quantas linhas foram agrupadas. As linhas originais continuam nas abas `Limpo_<base>`.
- As colunas do consolidado seguem o padrão `campo__base` para você saber de onde veio cada informação.
