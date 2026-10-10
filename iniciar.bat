@echo off
REM Abre o agente na tela web usando o servidor de IA iron-cody-api.
REM A chave e pedida agora e fica so na memoria desta janela (nao e gravada em arquivo).
cd /d "%~dp0"
set AGENTE_API_URL=https://iron-cody-api.fly.dev/v1
set /p ANTHROPIC_API_KEY=Cole a chave da API e tecle Enter (ou so Enter para rodar sem IA): 
python -m pip install -q -r requirements.txt
python -m streamlit run app.py
pause
