#!/usr/bin/env bash
# Abre o agente na tela web usando o servidor de IA iron-cody-api.
# A chave é pedida agora e fica só na memória deste terminal (não é gravada em arquivo).
cd "$(dirname "$0")"
export AGENTE_API_URL="https://iron-cody-api.fly.dev/v1"
read -r -s -p "Cole a chave da API e tecle Enter (ou só Enter para rodar sem IA): " ANTHROPIC_API_KEY
echo
[ -n "$ANTHROPIC_API_KEY" ] && export ANTHROPIC_API_KEY
python3 -m pip install -q -r requirements.txt
python3 -m streamlit run app.py
