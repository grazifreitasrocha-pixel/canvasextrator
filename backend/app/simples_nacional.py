"""
Cálculo do Simples Nacional (DAS) com segregação de receita.

Regra oficial (LC 123/2006 + Resolução CGSN 140/2018): quando um produto
já teve o ICMS retido por Substituição Tributária, a receita da revenda
continua entrando no RBT12 e na receita bruta do PGDAS-D, MAS a parcela
de ICMS não é cobrada de novo dentro do DAS — se abate o percentual de
ICMS da alíquota efetiva só para aquela fatia de receita. O mesmo vale
para produtos no regime monofásico de PIS/COFINS: abate-se o percentual
de PIS+COFINS da alíquota efetiva só para essa fatia.

Fórmula geral:
    Alíquota efetiva = (RBT12 x Alíquota nominal - Parcela a deduzir) / RBT12

Segregação (o que este módulo calcula):
    Receita normal              -> alíquota efetiva cheia
    Receita com ST-ICMS         -> alíquota efetiva - % ICMS da faixa
    Receita com monofásico      -> alíquota efetiva - % PIS - % COFINS da faixa
    Receita com ST + monofásico -> alíquota efetiva - % ICMS - % PIS - % COFINS

Fonte das tabelas: LC 123/2006, tabelas vigentes para o ano-calendário de
2026 (antes das mudanças graduais da Reforma Tributária, que começam a
afetar o Simples Nacional a partir de 2029). Se este módulo for usado em
anos futuros, as tabelas precisam ser conferidas e atualizadas.
"""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class FaixaSimples:
    faixa: int
    limite_superior: float          # RBT12 até este valor pertence a esta faixa
    aliquota_nominal: float          # em fração (0.04 = 4%)
    parcela_deduzir: float           # em R$
    pct_icms: float                  # fração da alíquota efetiva correspondente a ICMS (0 na faixa 6)
    pct_pis: float                   # fração da alíquota efetiva correspondente a PIS
    pct_cofins: float                # fração da alíquota efetiva correspondente a COFINS


# Anexo I — Comércio (tabela vigente em 2026, LC 123/2006)
TABELA_ANEXO_I = [
    FaixaSimples(1,   180_000.00, 0.0400,       0.00, 0.3400, 0.0276, 0.1274),
    FaixaSimples(2,   360_000.00, 0.0730,   5_940.00, 0.3400, 0.0276, 0.1274),
    FaixaSimples(3,   720_000.00, 0.0950,  13_860.00, 0.3350, 0.0276, 0.1274),
    FaixaSimples(4, 1_800_000.00, 0.1070,  22_500.00, 0.3350, 0.0276, 0.1274),
    FaixaSimples(5, 3_600_000.00, 0.1430,  87_300.00, 0.3350, 0.0276, 0.1274),
    # Faixa 6: ICMS sai do DAS (recolhido à parte, por fora) — por isso 0% aqui.
    # PIS/COFINS somados totalizam 34,40% da alíquota efetiva nesta faixa.
    FaixaSimples(6, 4_800_000.00, 0.1900, 378_000.00, 0.0000, 0.0613, 0.2827),
]

# Anexo II — Indústria (tabela vigente em 2026, LC 123/2006)
TABELA_ANEXO_II = [
    FaixaSimples(1,   180_000.00, 0.0450,       0.00, 0.3200, 0.0249, 0.1151),
    FaixaSimples(2,   360_000.00, 0.0780,   5_940.00, 0.3200, 0.0249, 0.1151),
    FaixaSimples(3,   720_000.00, 0.1000,  13_860.00, 0.3200, 0.0249, 0.1151),
    FaixaSimples(4, 1_800_000.00, 0.1120,  22_500.00, 0.3200, 0.0249, 0.1151),
    FaixaSimples(5, 3_600_000.00, 0.1470,  85_500.00, 0.3200, 0.0249, 0.1151),
    FaixaSimples(6, 4_800_000.00, 0.3000, 720_000.00, 0.0000, 0.0454, 0.2096),
]

TABELAS_POR_ANEXO = {
    "I": TABELA_ANEXO_I,
    "II": TABELA_ANEXO_II,
}


@dataclass
class ResultadoApuracao:
    anexo: str
    rbt12: float
    faixa: int
    aliquota_nominal: float
    parcela_deduzir: float
    aliquota_efetiva: float          # cheia, sem segregação
    aliquota_efetiva_st: float       # aplicável à receita só com ST-ICMS
    aliquota_efetiva_monofasico: float   # aplicável à receita só com monofásico
    aliquota_efetiva_st_e_monofasico: float  # aplicável à receita com os dois
    receita_normal: float
    receita_st: float
    receita_monofasico: float
    receita_st_e_monofasico: float
    receita_total_mes: float
    das_receita_normal: float
    das_receita_st: float
    das_receita_monofasico: float
    das_receita_st_e_monofasico: float
    das_total: float


def _localizar_faixa(rbt12: float, tabela: list) -> FaixaSimples:
    for faixa in tabela:
        if rbt12 <= faixa.limite_superior:
            return faixa
    # Acima do limite do Simples Nacional (R$ 4,8 milhões) — usa a última
    # faixa como aproximação, mas isso já indica desenquadramento do regime.
    return tabela[-1]


def calcular_aliquota_efetiva(rbt12: float, anexo: str = "I") -> dict:
    """
    Calcula a alíquota efetiva cheia e as três variantes segregadas
    (ST, monofásico, ambos) para o RBT12 informado.
    """
    if anexo not in TABELAS_POR_ANEXO:
        raise ValueError(f"Anexo '{anexo}' não suportado. Use um de: {list(TABELAS_POR_ANEXO.keys())}")
    if rbt12 <= 0:
        raise ValueError("RBT12 precisa ser maior que zero.")

    tabela = TABELAS_POR_ANEXO[anexo]
    faixa = _localizar_faixa(rbt12, tabela)

    aliquota_efetiva = (rbt12 * faixa.aliquota_nominal - faixa.parcela_deduzir) / rbt12
    aliquota_efetiva = max(aliquota_efetiva, 0.0)  # nunca negativa

    # IMPORTANTE: os percentuais da tabela (ex: 33,5% de ICMS) são uma
    # FRAÇÃO da própria alíquota efetiva, não um valor em pontos percentuais
    # a subtrair diretamente. Por isso o cálculo é multiplicativo:
    #   alíquota sem ICMS = alíquota efetiva × (1 - percentual de ICMS)
    # e não "alíquota efetiva - percentual de ICMS" (que misturaria escalas
    # diferentes e daria resultado errado).
    aliquota_st = aliquota_efetiva * (1 - faixa.pct_icms)
    aliquota_mono = aliquota_efetiva * (1 - faixa.pct_pis - faixa.pct_cofins)
    aliquota_st_mono = aliquota_efetiva * (1 - faixa.pct_icms - faixa.pct_pis - faixa.pct_cofins)

    return {
        "faixa": faixa.faixa,
        "aliquota_nominal": faixa.aliquota_nominal,
        "parcela_deduzir": faixa.parcela_deduzir,
        "aliquota_efetiva": aliquota_efetiva,
        "aliquota_efetiva_st": aliquota_st,
        "aliquota_efetiva_monofasico": aliquota_mono,
        "aliquota_efetiva_st_e_monofasico": aliquota_st_mono,
    }


def apurar_das_do_mes(
    resultados_analise: list,
    rbt12: float,
    anexo: str = "I",
) -> ResultadoApuracao:
    """
    Segrega a receita do mês (a partir dos ResultadoItem já cruzados pelo
    motor_analise.py, cada um com `valor_total` da nota) em 4 grupos e
    calcula o DAS de cada um com a alíquota correta.

    resultados_analise: lista de ResultadoItem (precisam ter o atributo
        `valor_total` preenchido — vem do parser_nfe.py)
    rbt12: receita bruta acumulada dos últimos 12 meses, informada pelo
        usuário a partir do PGDAS-D do mês anterior
    anexo: "I" (Comércio) ou "II" (Indústria)
    """
    calc = calcular_aliquota_efetiva(rbt12, anexo)

    receita_normal = 0.0
    receita_st = 0.0
    receita_mono = 0.0
    receita_st_mono = 0.0

    for r in resultados_analise:
        valor = getattr(r, "valor_total", None) or 0.0
        tem_st = r.sujeito_st_icms
        tem_mono = r.regime_monofasico is not None

        if tem_st and tem_mono:
            receita_st_mono += valor
        elif tem_st:
            receita_st += valor
        elif tem_mono:
            receita_mono += valor
        else:
            receita_normal += valor

    das_normal = receita_normal * calc["aliquota_efetiva"]
    das_st = receita_st * calc["aliquota_efetiva_st"]
    das_mono = receita_mono * calc["aliquota_efetiva_monofasico"]
    das_st_mono = receita_st_mono * calc["aliquota_efetiva_st_e_monofasico"]

    return ResultadoApuracao(
        anexo=anexo,
        rbt12=rbt12,
        faixa=calc["faixa"],
        aliquota_nominal=calc["aliquota_nominal"],
        parcela_deduzir=calc["parcela_deduzir"],
        aliquota_efetiva=calc["aliquota_efetiva"],
        aliquota_efetiva_st=calc["aliquota_efetiva_st"],
        aliquota_efetiva_monofasico=calc["aliquota_efetiva_monofasico"],
        aliquota_efetiva_st_e_monofasico=calc["aliquota_efetiva_st_e_monofasico"],
        receita_normal=receita_normal,
        receita_st=receita_st,
        receita_monofasico=receita_mono,
        receita_st_e_monofasico=receita_st_mono,
        receita_total_mes=receita_normal + receita_st + receita_mono + receita_st_mono,
        das_receita_normal=das_normal,
        das_receita_st=das_st,
        das_receita_monofasico=das_mono,
        das_receita_st_e_monofasico=das_st_mono,
        das_total=das_normal + das_st + das_mono + das_st_mono,
    )


if __name__ == "__main__":
    # Teste rápido com números redondos, fácil de conferir na mão
    calc = calcular_aliquota_efetiva(rbt12=1_000_000.00, anexo="I")
    print("Faixa:", calc["faixa"])
    print(f"Alíquota efetiva cheia: {calc['aliquota_efetiva']*100:.4f}%")
    print(f"Alíquota efetiva (só ST): {calc['aliquota_efetiva_st']*100:.4f}%")
    print(f"Alíquota efetiva (só monofásico): {calc['aliquota_efetiva_monofasico']*100:.4f}%")
    print(f"Alíquota efetiva (ST + monofásico): {calc['aliquota_efetiva_st_e_monofasico']*100:.4f}%")

    # Conferência manual: RBT12 = 1.000.000 está na faixa 4 (até 1.800.000)
    # Aliq efetiva = (1.000.000 * 0,1070 - 22.500) / 1.000.000 = 0,0845 = 8,45%
    assert abs(calc["aliquota_efetiva"] - 0.0845) < 0.0001, "Conferência manual falhou!"
    print("\nConferência manual (alíquota cheia): OK (8,45% esperado)")

    # Conferência da segregação por ST: 8,45% x (1 - 0,335) = 8,45% x 0,665 = 5,61925%
    esperado_st = 0.0845 * (1 - 0.335)
    assert abs(calc["aliquota_efetiva_st"] - esperado_st) < 0.00001, "Conferência de ST falhou!"
    print(f"Conferência manual (alíquota ST): OK ({esperado_st*100:.4f}% esperado)")

    # Conferência da segregação por monofásico: 8,45% x (1 - 0,0276 - 0,1274) = 8,45% x 0,845
    esperado_mono = 0.0845 * (1 - 0.0276 - 0.1274)
    assert abs(calc["aliquota_efetiva_monofasico"] - esperado_mono) < 0.00001, "Conferência de monofásico falhou!"
    print(f"Conferência manual (alíquota monofásico): OK ({esperado_mono*100:.4f}% esperado)")

    print("\n--- Teste completo de apuração segregada ---")
    from motor_analise import ResultadoItem

    def _item_fake(valor, st=False, mono=None):
        return ResultadoItem(
            descricao_produto="teste", ncm="00000000", ncm_valido=True,
            tipi_descricao=None, tipi_aliquota_ipi=None,
            tem_beneficio_icms=False, beneficios_icms=[],
            tem_beneficio_piscofins=False, beneficios_piscofins=[],
            regime_monofasico=mono, sujeito_st_icms=st, detalhe_st=[],
            analise="", valor_total=valor,
        )

    resultados_fake = [
        _item_fake(10000, st=False, mono=None),   # receita normal
        _item_fake(5000, st=True, mono=None),     # só ST
        _item_fake(3000, st=False, mono="algo"),  # só monofásico (mock não-None)
        _item_fake(2000, st=True, mono="algo"),   # ST + monofásico
    ]

    apuracao = apurar_das_do_mes(resultados_fake, rbt12=1_000_000.00, anexo="I")
    print(f"Receita normal: R$ {apuracao.receita_normal:.2f} | DAS: R$ {apuracao.das_receita_normal:.2f}")
    print(f"Receita ST: R$ {apuracao.receita_st:.2f} | DAS: R$ {apuracao.das_receita_st:.2f}")
    print(f"Receita monofásico: R$ {apuracao.receita_monofasico:.2f} | DAS: R$ {apuracao.das_receita_monofasico:.2f}")
    print(f"Receita ST+mono: R$ {apuracao.receita_st_e_monofasico:.2f} | DAS: R$ {apuracao.das_receita_st_e_monofasico:.2f}")
    print(f"DAS TOTAL: R$ {apuracao.das_total:.2f}")
