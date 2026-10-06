"""Agente de organização e sincronização de dados governamentais."""

from .pipeline import ResultadoAgente, executar, gerar_excel, gerar_pacote_zip

__all__ = ["ResultadoAgente", "executar", "gerar_excel", "gerar_pacote_zip"]
