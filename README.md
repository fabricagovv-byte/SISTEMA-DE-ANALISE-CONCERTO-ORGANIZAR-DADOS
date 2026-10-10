# 🏛️ Agente de Organização e Sincronização de Dados do Governo

Você sobe **documentos públicos** (PDF, Word, HTML, TXT, RTF) e **bases de dados** (CSV, Excel, JSON) — tudo junto — e o agente:

### 🗂️ Pasta ou ZIP desorganizado (envie a pasta inteira compactada)
- **Estrutura**: remove lixo (`Thumbs.db`, `~$` do Office, `.DS_Store`, vazios), acha **arquivos idênticos** (SHA-256) mesmo em pastas de backup, aponta nomes ruins (`Nova pasta (2)`, `sem título`, `final final`, `v2_AGORA_VAI`, `(cópia)`) com nome sugerido, e põe `.env`/`senhas*.txt`/chaves em **quarentena** (analisados, nunca copiados para a saída).
- **Leitura robusta**: Latin-1 ou UTF-8, `;` ou `,`, cabeçalho fora da linha 1, título na A1, espaços no cabeçalho, linhas vazias e de TOTAL no meio, abas de rascunho, JSON com esquemas diferentes, acentos quebrados (`JoÃ£o` → `João`).
- **Base única de pessoas**: junta o mesmo cliente/beneficiário vindo de vários arquivos (CPF, e-mail, nome ou ID, mesmo com colunas de nomes diferentes como `documento` = CPF), escolhe campo a campo o melhor valor válido da fonte mais confiável (backups antigos valem menos), **corrige CPF inválido com o valor válido de outra fonte** e lista todos os conflitos.
- **Padronização**: datas ISO (detecta formato americano MM/DD e sinaliza datas ambíguas), CPF validado e formatado, telefone `+55 (DD) NNNNN-NNNN`, e-mail minúsculo e validado (`@@` corrigido), nomes e cidades padronizados (`POA` → Porto Alegre), sim/não, valores com moeda (R$/USD).
- **Transações ligadas ao ID** (vendas, pedidos...): o cliente pode vir por ID, nome completo, só primeiro nome, e-mail ou objeto aninhado com CPF.
- **Segredos**: senhas em texto puro, chaves de API, `.env`, strings de conexão, IP interno, **números de cartão (validados por Luhn)** — risco CRÍTICO, removidos de todas as saídas.

### 🏪 Vendas, estoque, fornecedores e pagamentos
- **Formatos**: CSV (UTF-8, Latin-1, **UTF-16 com TAB**), Excel (fórmulas sem resultado salvo, linha de SOMA no meio, **abas ocultas**), **XML**, **SQLite (.db)**, **dump SQL**, **JSONL** (com linha quebrada), JSON com **CSV em base64** escondido, **e-mail .eml**, **log**, YAML, ZIP dentro de ZIP.
- **Vendas de várias fontes numa base única**, sem contar duas vezes: o mesmo pedido no caixa manual e no e-commerce, pedidos repetidos com outro código (`WEB-00052` = `PV00052`), valores em **centavos** (confirmado contra o preço do catálogo), horário **UTC → Brasília**, datas em serial do Excel/epoch/sem ano, cliente por ID, nome, CPF ou e-mail antigo/novo. Vendas órfãs, inválidas ou de cliente **homônimo (ambíguo)** ficam listadas à parte.
- **Estoque**: catálogo com preço em `R$`, decimal ou centavos e peso em g/kg; registros deletados ignorados; saldo por SKU com **negativos sinalizados**; movimentos de SKU inexistente ou com data futura separados.
- **Fornecedores**: duplicados unificados, CNPJ validado.
- **Conciliação**: pagamentos × vendas, valores divergentes, estornos e chargebacks.
- **Cadastros separados**: leads (com consentimento LGPD) e colaboradores não se misturam com clientes; homônimos com IDs/CPFs diferentes nunca são unidos.

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

**Jeito mais fácil:** dê dois cliques em `iniciar.bat` (Windows) ou rode `./iniciar.sh` (Linux/Mac). Ele instala o que falta, pede a chave da IA (já configurado para o servidor `iron-cody-api.fly.dev`) e abre a tela no navegador. A chave não é gravada em arquivo.

```bash
pip install -r requirements.txt

# Interface web (suba os arquivos pelo navegador)
streamlit run app.py

# Ou pela linha de comando
python -m agente exemplos/* -o resultado.xlsx     # gera resultado.xlsx e resultado.zip
python -m agente pasta_baguncada/ -o resultado.xlsx   # uma pasta inteira (ou um .zip)
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

**Usando um gateway/servidor próprio compatível com a API da Anthropic:**

```bash
export AGENTE_API_URL="https://seu-gateway/v1"      # Windows: set AGENTE_API_URL=https://seu-gateway/v1
export ANTHROPIC_API_KEY="chave-do-gateway"
python -m agente pasta.zip --ia -o resultado.xlsx    # ou --api-url https://seu-gateway/v1
```

Na tela web (`streamlit run app.py`) há os campos **Endereço da API** e **Chave da API** na barra lateral. Com gateway, o agente usa um "modo compatível" (sem recursos beta; JSON pedido no texto). Atenção: o gateway recebe o que é enviado à IA — o agente já manda tudo anonimizado, mas use apenas serviços em que você confia.

**Privacidade (LGPD):** a IA **nunca recebe dados pessoais em claro**. Das tabelas vão só os nomes das colunas e até 5 exemplos por coluna com números longos mascarados (`###`); dos documentos vai o texto **já anonimizado** (`[CPF]`, `[NOME DE PESSOA]`...); para o parecer, apenas estatísticas agregadas.

**Limitações:** PDFs digitalizados (imagem) precisam de OCR antes — o agente avisa quando encontra um. A detecção de nomes depende de contexto ("Sr.", "representada por", "beneficiária", "Nome:"); nomes soltos no meio do texto podem escapar, então revise documentos de risco ALTO antes de publicar.

## Estrutura

```
agente/
  inventario.py     pasta/ZIP: lixo, duplicatas, nomes ruins, quarentena de segredos
  entidades.py      base única de pessoas, conflitos e transações ligadas ao ID
  dominios.py       vendas unificadas, produtos/estoque, fornecedores e conciliação de pagamentos
  leitor.py         leitura de CSV/Excel/JSON (cabeçalho deslocado, TOTAL, rascunho, encoding)
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
tests/              testes automáticos (python -m pytest), incl. testes de aceitação com banco_desorganizado.zip e empresa_dados_caos.zip
```

## Regras do cruzamento

- Linhas sem a chave ficam fora do consolidado (e isso é avisado).
- Se a mesma chave aparece várias vezes numa base (ex.: vários pagamentos para o mesmo CPF), os **valores em R$ são somados**, os demais campos ficam com o primeiro preenchido e a coluna `qtd_registros__<base>` mostra quantas linhas foram agrupadas. As linhas originais continuam nas abas `Limpo_<base>`.
- As colunas do consolidado seguem o padrão `campo__base` para você saber de onde veio cada informação.
