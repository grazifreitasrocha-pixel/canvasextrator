#!/usr/bin/env python3
"""
Script principal — roda o pipeline completo fora do ambiente de desenvolvimento,
simulando o que o sistema fará quando estiver finalizado (sem interface web ainda,
mas com a mesma lógica de ponta a ponta).

USO:
    python processar_lote.py notas.zip

    python processar_lote.py notas.zip --saida resultado.xlsx

    # Com validação por IA (requer ANTHROPIC_API_KEY configurada):
    python processar_lote.py notas.zip --com-ia

O QUE ELE FAZ:
    1. Lê o ZIP de XMLs de NF-e e extrai descrição do produto + NCM de cada item
    2. Carrega as bases já processadas (TIPI, benefícios de ICMS, PIS/COFINS
       monofásico) da pasta bases/
    3. Cruza cada item contra essas bases
    4. (Opcional) Roda a validação por IA da coerência da classificação
    5. Gera a planilha final em Excel
"""

import argparse
import json
import os
import sys
from pathlib import Path

from parser_nfe import processar_zip_nfe, extrair_itens_flat
from motor_analise import analisar_lote
from gerar_planilha import gerar_planilha_resultado

PASTA_BASES = Path(__file__).parent / "bases"


def carregar_bases():
    """Carrega as bases de referência já processadas (JSON) da pasta bases/."""
    def _carregar(nome_arquivo):
        caminho = PASTA_BASES / nome_arquivo
        if not caminho.exists():
            print(f"AVISO: {nome_arquivo} não encontrado em {PASTA_BASES} — seguindo sem essa base.")
            return {} if nome_arquivo == "tipi.json" else []
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)

    return {
        "tipi": _carregar("tipi.json"),
        "beneficios_icms": _carregar("beneficios_icms.json"),
        "beneficios_piscofins": _carregar("beneficios_piscofins.json"),
        "monofasico": _carregar("monofasico_piscofins.json"),
        "substituicao_tributaria": _carregar("substituicao_tributaria.json"),
    }


def main():
    parser = argparse.ArgumentParser(description="Processa um ZIP de NF-e e gera a planilha de análise fiscal.")
    parser.add_argument("zip_notas", help="Caminho do ZIP contendo os XMLs de NF-e")
    parser.add_argument("--saida", default="resultado_analise.xlsx", help="Nome do arquivo Excel de saída")
    parser.add_argument("--com-ia", action="store_true", help="Roda também a validação de coerência da classificação via IA (requer ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    if not os.path.exists(args.zip_notas):
        print(f"ERRO: arquivo não encontrado: {args.zip_notas}")
        sys.exit(1)

    print(f"1/5 — Lendo XMLs do ZIP: {args.zip_notas}")
    resultado_zip = processar_zip_nfe(args.zip_notas)
    print(f"      {resultado_zip['total_arquivos']} XMLs encontrados, "
          f"{resultado_zip['total_processados']} processados, "
          f"{resultado_zip['total_erros']} com erro.")
    if resultado_zip["erros"]:
        print("      Arquivos com erro:")
        for e in resultado_zip["erros"][:10]:
            print(f"        - {e['arquivo']}: {e['erro']}")

    itens = extrair_itens_flat(resultado_zip)
    print(f"      Total de itens (produtos) extraídos: {len(itens)}")

    if not itens:
        print("Nenhum item para processar. Encerrando.")
        sys.exit(0)

    print("2/5 — Carregando bases de referência (TIPI, benefícios, regimes especiais)")
    bases = carregar_bases()
    print(f"      TIPI: {len(bases['tipi'])} NCMs")
    print(f"      Benefícios ICMS: {len(bases['beneficios_icms'])} regras")
    print(f"      Benefícios PIS/COFINS: {len(bases['beneficios_piscofins'])} regras")
    print(f"      Regime monofásico PIS/COFINS: {len(bases['monofasico'])} regras")
    print(f"      Substituição Tributária ICMS: {len(bases['substituicao_tributaria'])} regras")

    print("3/5 — Cruzando itens contra as bases")
    beneficios_cadastrados = bases["beneficios_icms"] + bases["beneficios_piscofins"]
    resultados = analisar_lote(
        itens, bases["tipi"], beneficios_cadastrados,
        bases["monofasico"], bases["substituicao_tributaria"],
    )
    print(f"      {len(resultados)} itens analisados.")

    pareceres_ia = {}
    if args.com_ia:
        print("4/5 — Rodando validação de coerência via IA (pode levar alguns minutos)...")
        try:
            from classificador_ia import validar_lote_com_ia, montar_dicionario_pareceres
            itens_para_ia = [
                {
                    "descricao_produto": r.descricao_produto,
                    "ncm": r.ncm,
                    "tipi_descricao": r.tipi_descricao,
                }
                for r in resultados
            ]
            pareceres = validar_lote_com_ia(itens_para_ia)
            pareceres_ia = montar_dicionario_pareceres(pareceres)
            incoerentes = sum(1 for p in pareceres if not p.classificacao_coerente)
            print(f"      Concluído. {incoerentes} item(ns) sinalizado(s) como possivelmente incoerente(s).")
        except Exception as e:
            print(f"      AVISO: falha na validação por IA ({e}). Seguindo sem essa etapa.")
    else:
        print("4/5 — Validação por IA não solicitada (use --com-ia para ativar).")

    print(f"5/5 — Gerando planilha final: {args.saida}")
    gerar_planilha_resultado(resultados, args.saida, pareceres_ia)
    print(f"\nConcluído! Planilha salva em: {os.path.abspath(args.saida)}")


if __name__ == "__main__":
    main()
