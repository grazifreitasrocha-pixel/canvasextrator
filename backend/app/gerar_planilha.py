"""
Gera a planilha final (.xlsx) a partir dos resultados do motor de análise.

Colunas: Descrição do Produto | NCM | Tem Benefício (S/N) | Detalhe do Benefício/Norma
         | Descrição TIPI | Alíquota IPI | Análise | Alerta
"""

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


def gerar_planilha_resultado(resultados: list, caminho_saida: str, pareceres_ia: dict = None):
    """
    resultados: lista de ResultadoItem (do motor_analise.py)
    caminho_saida: caminho do .xlsx a ser gerado
    pareceres_ia: dict opcional {ncm+descricao: ParecerIA}, do classificador_ia.py.
                  Se None, as colunas de IA saem em branco/"-".
    """
    pareceres_ia = pareceres_ia or {}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Análise NCM x Benefícios"

    cabecalho = [
        "Descrição do Produto",
        "NCM",
        "NCM Válido (TIPI)",
        "Tem Benefício ICMS",
        "Detalhe do Benefício ICMS",
        "Sujeito a ST-ICMS",
        "Detalhe da ST-ICMS",
        "Tem Benefício PIS/COFINS",
        "Detalhe do Benefício PIS/COFINS",
        "Regime Monofásico PIS/COFINS",
        "Detalhe do Regime Monofásico",
        "Descrição TIPI",
        "Alíquota IPI",
        "Análise",
        "Classificação Coerente (IA)",
        "Confiança (IA)",
        "Parecer da IA",
        "NCM Sugerido (IA)",
        "Alerta",
    ]
    ws.append(cabecalho)

    # Estilo do cabeçalho
    fonte_cabecalho = Font(bold=True, color="FFFFFF")
    fundo_cabecalho = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    for col_idx in range(1, len(cabecalho) + 1):
        celula = ws.cell(row=1, column=col_idx)
        celula.font = fonte_cabecalho
        celula.fill = fundo_cabecalho
        celula.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    fundo_sim = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fundo_nao = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    fundo_alerta = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    fundo_incoerente = PatternFill(start_color="FFC000", end_color="FFC000", fill_type="solid")
    fundo_regime_especial = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")  # azul: ST/monofásico (não é benefício, é regime especial)

    for r in resultados:
        detalhe_icms = " | ".join(
            f"{b.tipo_beneficio.upper()} - {b.norma_titulo}" +
            (f" [{b.condicoes}]" if b.condicoes else "")
            for b in r.beneficios_icms
        ) if r.beneficios_icms else "-"

        detalhe_piscofins = " | ".join(
            f"{b.tipo_beneficio.upper()} - {b.norma_titulo}" +
            (f" [{b.condicoes}]" if b.condicoes else "")
            for b in r.beneficios_piscofins
        ) if r.beneficios_piscofins else "-"

        detalhe_st = " | ".join(
            f"CEST {s.cest or '-'} / UF {s.uf or '-'}" +
            (f" / MVA {s.mva_percentual}%" if s.mva_percentual is not None else "") +
            (f" - {s.norma_titulo}" if s.norma_titulo else "")
            for s in r.detalhe_st
        ) if r.detalhe_st else "-"

        if r.regime_monofasico:
            rm = r.regime_monofasico
            if rm.aliquota_zero:
                detalhe_monofasico = f"Alíquota ZERO (revenda) - código {rm.codigo_sped} - {rm.origem_tabela}"
            else:
                detalhe_monofasico = (
                    f"PIS {rm.aliquota_pis}% / COFINS {rm.aliquota_cofins}% "
                    f"- código {rm.codigo_sped} - {rm.origem_tabela}"
                )
        else:
            detalhe_monofasico = "-"

        chave_ia = f"{r.ncm}|{r.descricao_produto}"
        parecer = pareceres_ia.get(chave_ia)

        if parecer:
            coerente_txt = "Sim" if parecer.classificacao_coerente else "NÃO"
            confianca_txt = parecer.nivel_confianca
            justificativa_txt = parecer.justificativa
            ncm_sugerido_txt = parecer.ncm_sugerido or "-"
        else:
            coerente_txt = "-"
            confianca_txt = "-"
            justificativa_txt = "-"
            ncm_sugerido_txt = "-"

        linha = [
            r.descricao_produto,
            r.ncm,
            "Sim" if r.ncm_valido else "Não",
            "Sim" if r.tem_beneficio_icms else "Não",
            detalhe_icms,
            "Sim" if r.sujeito_st_icms else "Não",
            detalhe_st,
            "Sim" if r.tem_beneficio_piscofins else "Não",
            detalhe_piscofins,
            "Sim" if r.regime_monofasico else "Não",
            detalhe_monofasico,
            r.tipi_descricao or "-",
            r.tipi_aliquota_ipi or "-",
            r.analise,
            coerente_txt,
            confianca_txt,
            justificativa_txt,
            ncm_sugerido_txt,
            r.alerta or "-",
        ]
        ws.append(linha)

        linha_atual = ws.max_row
        ws.cell(row=linha_atual, column=4).fill = fundo_sim if r.tem_beneficio_icms else fundo_nao
        ws.cell(row=linha_atual, column=6).fill = fundo_regime_especial if r.sujeito_st_icms else fundo_nao
        ws.cell(row=linha_atual, column=8).fill = fundo_sim if r.tem_beneficio_piscofins else fundo_nao
        ws.cell(row=linha_atual, column=10).fill = fundo_regime_especial if r.regime_monofasico else fundo_nao

        if r.alerta:
            for col_idx in range(1, len(cabecalho) + 1):
                ws.cell(row=linha_atual, column=col_idx).fill = fundo_alerta

        # Destaque forte se a IA apontou classificação incoerente (sobrepõe outros alertas)
        if parecer and not parecer.classificacao_coerente:
            for col_idx in range(1, len(cabecalho) + 1):
                ws.cell(row=linha_atual, column=col_idx).fill = fundo_incoerente

    # Ajusta largura das colunas
    larguras = [35, 12, 14, 14, 40, 14, 40, 16, 40, 16, 40, 35, 12, 50, 16, 12, 45, 16, 35]
    for idx, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = largura

    ws.freeze_panes = "A2"
    wb.save(caminho_saida)
    return caminho_saida


if __name__ == "__main__":
    # Teste rápido usando o motor_analise em memória
    from motor_analise import analisar_lote

    tipi_exemplo = {
        "73181500": {"descricao": "Parafusos de ferro fundido, ferro ou aço", "aliquota_ipi": "5"},
        "30049099": {"descricao": "Outros medicamentos", "aliquota_ipi": "NT"},
    }
    beneficios_exemplo = [
        {
            "ncm": "3004", "ncm_prefixo": True,
            "norma_titulo": "Convênio ICMS 87/2002", "tributo": "ICMS",
            "tipo_beneficio": "isenção",
            "condicoes": "medicamentos de uso humano constantes na lista do convênio",
            "vigencia_fim": None,
        },
        {
            "ncm": "3004", "ncm_prefixo": True,
            "norma_titulo": "Lei 10.147/2000", "tributo": "PIS/COFINS",
            "tipo_beneficio": "alíquota zero",
            "condicoes": "medicamentos relacionados em ato do Poder Executivo",
            "vigencia_fim": None,
        },
    ]
    itens_exemplo = [
        {"descricao_produto": "PARAFUSO DE ACO INOX M6", "ncm": "73181500"},
        {"descricao_produto": "MEDICAMENTO GENERICO XYZ 500MG", "ncm": "30049099"},
        {"descricao_produto": "PRODUTO COM NCM INEXISTENTE", "ncm": "99999999"},
    ]

    resultados = analisar_lote(itens_exemplo, tipi_exemplo, beneficios_exemplo)
    caminho = gerar_planilha_resultado(resultados, "/home/claude/sistema-fiscal/data/teste_resultado.xlsx")
    print(f"Planilha gerada em: {caminho}")
