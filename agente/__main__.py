"""Uso: python -m agente arquivo1.csv arquivo2.xlsx [-o saida.xlsx] [--ia] [--chave cpf]"""

import argparse
from pathlib import Path

from .pipeline import executar, gerar_excel, gerar_pacote_zip


def main():
    parser = argparse.ArgumentParser(description="Organiza documentos e bases governamentais, acha continuações e dados sensíveis.")
    parser.add_argument("arquivos", nargs="+", help="Pastas, ZIP, CSV, XLSX, XLS, JSON, PDF, DOCX, HTML, TXT, MD ou RTF")
    parser.add_argument("-o", "--saida", default="resultado_organizado.xlsx")
    parser.add_argument("--ia", action="store_true", help="Usar Claude para entender colunas e redigir parecer")
    parser.add_argument("--chave", help="Forçar a chave de cruzamento (ex.: cpf, cnpj, codigo_ibge, matricula)")
    args = parser.parse_args()

    arquivos = []
    for a in args.arquivos:
        caminho = Path(a)
        if caminho.is_dir():
            # Pasta inteira: mantém o caminho relativo (o inventário analisa a estrutura).
            for f in sorted(p for p in caminho.rglob("*") if p.is_file()):
                arquivos.append((f.relative_to(caminho).as_posix(), f.read_bytes()))
        else:
            arquivos.append((caminho.name, caminho.read_bytes()))
    resultado = executar(arquivos, usar_ia=args.ia, chave_forcada=args.chave, progresso=print)
    Path(args.saida).write_bytes(gerar_excel(resultado))
    zip_saida = Path(args.saida).with_suffix(".zip")
    zip_saida.write_bytes(gerar_pacote_zip(resultado))
    print(f"\nPlanilha gerada: {args.saida}\nPacote (CSVs + versões anonimizadas): {zip_saida}\n")
    print(resultado.parecer)


if __name__ == "__main__":
    main()
