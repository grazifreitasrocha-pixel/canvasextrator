"""
Motor de cruzamento: NCM (do XML) x TIPI x Base de Benefícios Fiscais.

Para cada item extraído das notas fiscais, este módulo:
  1. Verifica se o NCM existe na TIPI vigente (detecta NCM extinto/inválido)
  2. Traz a descrição oficial e alíquota de IPI da TIPI
  3. Verifica se há benefício fiscal cadastrado pra aquele NCM
     (considerando também prefixos — posição/capítulo — quando a norma
     cobre uma faixa de NCMs, não só um código específico)
  4. Gera um texto de análise resumido

Este módulo assume que `tipi_por_ncm` e `beneficios` já foram carregados
(ex: de um banco de dados ou de uma lista processada pelos parsers).
Ele não depende de banco — funciona em memória, o que facilita testar
antes de plugar no Postgres.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class BeneficioEncontrado:
    norma_titulo: str
    tributo: str
    tipo_beneficio: str
    condicoes: Optional[str]
    vigencia_fim: Optional[str]  # None = vigente


@dataclass
class RegimeMonofasico:
    codigo_sped: str
    descricao_produto: str
    aliquota_pis: Optional[str]
    aliquota_cofins: Optional[str]
    aliquota_zero: bool
    origem_tabela: str


@dataclass
class SubstituicaoTributaria:
    cest: Optional[str]
    uf: Optional[str]
    mva_percentual: Optional[float]
    norma_titulo: Optional[str]
    observacoes: Optional[str]


@dataclass
class ResultadoItem:
    descricao_produto: str
    ncm: str
    ncm_valido: bool
    tipi_descricao: Optional[str]
    tipi_aliquota_ipi: Optional[str]
    tem_beneficio_icms: bool
    beneficios_icms: list          # list[BeneficioEncontrado]
    tem_beneficio_piscofins: bool
    beneficios_piscofins: list     # list[BeneficioEncontrado]
    regime_monofasico: Optional[RegimeMonofasico]   # None = não está sujeito ao regime monofásico
    sujeito_st_icms: bool
    detalhe_st: list               # list[SubstituicaoTributaria] (pode haver mais de uma regra por UF)
    analise: str
    alerta: Optional[str] = None


def _buscar_beneficios_para_ncm(ncm: str, beneficios_cadastrados: list) -> list:
    """
    Busca benefícios aplicáveis a um NCM, considerando:
      - match exato (NCM completo de 8 dígitos)
      - match por prefixo (quando a norma cobre uma posição/capítulo inteiro)

    `beneficios_cadastrados` é uma lista de dicts no formato:
        {
            "ncm": "3004" ou "30049099",
            "ncm_prefixo": True/False,
            "norma_titulo": "...",
            "tributo": "...",
            "tipo_beneficio": "...",
            "condicoes": "...",
            "vigencia_fim": "..." ou None,
        }
    """
    encontrados = []
    for b in beneficios_cadastrados:
        ncm_norma = b["ncm"]
        if b.get("ncm_prefixo"):
            if ncm.startswith(ncm_norma):
                encontrados.append(b)
        else:
            if ncm == ncm_norma:
                encontrados.append(b)
    return encontrados


def _buscar_regime_monofasico(ncm: str, tabela_monofasico: list) -> Optional[RegimeMonofasico]:
    """
    tabela_monofasico: lista de dicts {ncm, codigo_sped, descricao_produto,
        aliquota_pis, aliquota_cofins, aliquota_zero, origem_tabela}
    (formato de saída do parser_sped_piscofins.py)
    """
    for r in tabela_monofasico:
        if r.get("ncm") == ncm:
            return RegimeMonofasico(
                codigo_sped=r["codigo"],
                descricao_produto=r["descricao_produto"],
                aliquota_pis=r.get("aliquota_pis"),
                aliquota_cofins=r.get("aliquota_cofins"),
                aliquota_zero=r.get("aliquota_zero", False),
                origem_tabela=r.get("origem_tabela", ""),
            )
    return None


def _buscar_substituicao_tributaria(ncm: str, tabela_st: list) -> list:
    """
    tabela_st: lista de dicts {ncm, ncm_prefixo, cest, uf, mva_percentual,
        norma_titulo, observacoes}
    """
    encontrados = []
    for r in tabela_st:
        ncm_regra = r["ncm"]
        match = ncm.startswith(ncm_regra) if r.get("ncm_prefixo") else ncm == ncm_regra
        if match:
            encontrados.append(SubstituicaoTributaria(
                cest=r.get("cest"),
                uf=r.get("uf"),
                mva_percentual=r.get("mva_percentual"),
                norma_titulo=r.get("norma_titulo"),
                observacoes=r.get("observacoes"),
            ))
    return encontrados


def analisar_item(
    descricao_produto: str,
    ncm: str,
    tipi_por_ncm: dict,
    beneficios_cadastrados: list,
    tabela_monofasico: Optional[list] = None,
    tabela_st: Optional[list] = None,
) -> ResultadoItem:
    """
    Executa o cruzamento completo para um item (produto + NCM).

    tipi_por_ncm: dict {ncm: {"descricao": ..., "aliquota_ipi": ...}}
    beneficios_cadastrados: lista de benefícios (ver _buscar_beneficios_para_ncm),
        cada um com "tributo" igual a "ICMS" ou "PIS/COFINS".
    tabela_monofasico: lista do parser_sped_piscofins.py (opcional)
    tabela_st: lista de regras de substituição tributária de ICMS (opcional)
    """
    tabela_monofasico = tabela_monofasico or []
    tabela_st = tabela_st or []
    ncm = (ncm or "").strip()
    alerta = None

    info_tipi = tipi_por_ncm.get(ncm)
    ncm_valido = info_tipi is not None

    if not ncm_valido:
        alerta = "NCM não encontrado na TIPI vigente — verificar se foi extinto/substituído ou se há erro de digitação na nota."

    beneficios_raw = _buscar_beneficios_para_ncm(ncm, beneficios_cadastrados)

    def _to_beneficio(b):
        return BeneficioEncontrado(
            norma_titulo=b["norma_titulo"],
            tributo=b["tributo"],
            tipo_beneficio=b["tipo_beneficio"],
            condicoes=b.get("condicoes"),
            vigencia_fim=b.get("vigencia_fim"),
        )

    def _eh_icms(tributo: str) -> bool:
        return "ICMS" in tributo.upper()

    def _eh_piscofins(tributo: str) -> bool:
        tributo_upper = tributo.upper()
        return "PIS" in tributo_upper or "COFINS" in tributo_upper

    beneficios_icms = [_to_beneficio(b) for b in beneficios_raw if _eh_icms(b["tributo"])]
    beneficios_piscofins = [_to_beneficio(b) for b in beneficios_raw if _eh_piscofins(b["tributo"])]

    tem_beneficio_icms = len(beneficios_icms) > 0
    tem_beneficio_piscofins = len(beneficios_piscofins) > 0

    regime_monofasico = _buscar_regime_monofasico(ncm, tabela_monofasico)
    detalhe_st = _buscar_substituicao_tributaria(ncm, tabela_st)
    sujeito_st_icms = len(detalhe_st) > 0

    # Monta o texto de análise
    partes_analise = []

    if ncm_valido:
        aliquota = info_tipi.get("aliquota_ipi") or "não informada"
        partes_analise.append(
            f"NCM {ncm} consta na TIPI vigente como \"{info_tipi.get('descricao', '')}\", "
            f"com alíquota de IPI de {aliquota}."
        )
    else:
        partes_analise.append(f"NCM {ncm} não foi localizado na tabela TIPI vigente carregada no sistema.")

    if tem_beneficio_icms:
        for b in beneficios_icms:
            trecho = f"Possui benefício de ICMS ({b.tipo_beneficio}), conforme {b.norma_titulo}."
            if b.condicoes:
                trecho += f" Condição: {b.condicoes}."
            if b.vigencia_fim:
                trecho += f" Vigência até {b.vigencia_fim}."
            partes_analise.append(trecho)
    else:
        partes_analise.append("Nenhum benefício de ICMS cadastrado na base para este NCM até o momento.")

    if tem_beneficio_piscofins:
        for b in beneficios_piscofins:
            trecho = f"Possui benefício de PIS/COFINS ({b.tipo_beneficio}), conforme {b.norma_titulo}."
            if b.condicoes:
                trecho += f" Condição: {b.condicoes}."
            if b.vigencia_fim:
                trecho += f" Vigência até {b.vigencia_fim}."
            partes_analise.append(trecho)
    else:
        partes_analise.append("Nenhum benefício de PIS/COFINS cadastrado na base para este NCM até o momento.")

    if regime_monofasico:
        if regime_monofasico.aliquota_zero:
            partes_analise.append(
                f"Produto sujeito ao regime monofásico/pauta de PIS/COFINS com ALÍQUOTA ZERO "
                f"(código {regime_monofasico.codigo_sped} da {regime_monofasico.origem_tabela}) — "
                f"geralmente aplicável na revenda, pois a tributação já ocorreu em etapa anterior."
            )
        else:
            partes_analise.append(
                f"Produto sujeito ao regime monofásico/pauta de PIS/COFINS, com alíquotas concentradas "
                f"de PIS {regime_monofasico.aliquota_pis}% e COFINS {regime_monofasico.aliquota_cofins}% "
                f"(código {regime_monofasico.codigo_sped} da {regime_monofasico.origem_tabela}). "
                f"Não é isenção — é antecipação da tributação numa única etapa da cadeia."
            )
    else:
        partes_analise.append("Não identificado em regime monofásico/pauta de PIS/COFINS.")

    if sujeito_st_icms:
        ufs = ", ".join(sorted(set(s.uf for s in detalhe_st if s.uf))) or "não especificada(s)"
        partes_analise.append(
            f"Produto sujeito à Substituição Tributária de ICMS (UF: {ufs}). "
            f"Não é benefício — é antecipação do imposto pelo substituto tributário; "
            f"exige CST/CSOSN e, quando aplicável, MVA específicos."
        )
    else:
        partes_analise.append("Não identificado em regra de Substituição Tributária de ICMS cadastrada.")

    return ResultadoItem(
        descricao_produto=descricao_produto,
        ncm=ncm,
        ncm_valido=ncm_valido,
        tipi_descricao=info_tipi.get("descricao") if info_tipi else None,
        tipi_aliquota_ipi=info_tipi.get("aliquota_ipi") if info_tipi else None,
        tem_beneficio_icms=tem_beneficio_icms,
        beneficios_icms=beneficios_icms,
        tem_beneficio_piscofins=tem_beneficio_piscofins,
        beneficios_piscofins=beneficios_piscofins,
        regime_monofasico=regime_monofasico,
        sujeito_st_icms=sujeito_st_icms,
        detalhe_st=detalhe_st,
        analise=" ".join(partes_analise),
        alerta=alerta,
    )


def analisar_lote(
    itens: list,
    tipi_por_ncm: dict,
    beneficios_cadastrados: list,
    tabela_monofasico: Optional[list] = None,
    tabela_st: Optional[list] = None,
) -> list:
    """
    Roda `analisar_item` para uma lista de itens extraídos do XML.

    itens: lista de dicts com pelo menos {"descricao_produto": ..., "ncm": ...}
    Retorna lista de ResultadoItem.
    """
    return [
        analisar_item(
            item["descricao_produto"], item["ncm"], tipi_por_ncm,
            beneficios_cadastrados, tabela_monofasico, tabela_st,
        )
        for item in itens
    ]


if __name__ == "__main__":
    # Teste rápido em memória, sem banco, só pra validar a lógica
    tipi_exemplo = {
        "73181500": {"descricao": "Parafusos de ferro fundido, ferro ou aço", "aliquota_ipi": "5"},
        "30049099": {"descricao": "Outros medicamentos", "aliquota_ipi": "NT"},
    }

    beneficios_exemplo = [
        {
            "ncm": "3004",
            "ncm_prefixo": True,
            "norma_titulo": "Convênio ICMS 87/2002",
            "tributo": "ICMS",
            "tipo_beneficio": "isenção",
            "condicoes": "medicamentos de uso humano constantes na lista do convênio",
            "vigencia_fim": None,
        },
        {
            "ncm": "3004",
            "ncm_prefixo": True,
            "norma_titulo": "Lei 10.147/2000",
            "tributo": "PIS/COFINS",
            "tipo_beneficio": "alíquota zero",
            "condicoes": "medicamentos relacionados em ato do Poder Executivo",
            "vigencia_fim": None,
        },
    ]

    itens_exemplo = [
        {"descricao_produto": "PARAFUSO DE ACO INOX M6", "ncm": "73181500"},
        {"descricao_produto": "MEDICAMENTO GENERICO XYZ 500MG", "ncm": "30049099"},
        {"descricao_produto": "PRODUTO COM NCM INEXISTENTE", "ncm": "99999999"},
        {"descricao_produto": "AGUA MINERAL 10L GALAO", "ncm": "22011000"},
    ]

    tabela_monofasico_exemplo = [
        {
            "ncm": "22011000", "codigo": "822",
            "descricao_produto": "Águas Minerais Naturais Envasadas em Embalagens >= 10 Litros",
            "aliquota_pis": "0,00", "aliquota_cofins": "0,00", "aliquota_zero": True,
            "origem_tabela": "Tabela 4.3.11",
        },
    ]

    tabela_st_exemplo = [
        {
            "ncm": "73181500", "ncm_prefixo": False, "cest": "10.123.00", "uf": "GO",
            "mva_percentual": 40.0, "norma_titulo": "Protocolo ICMS 41/2008",
            "observacoes": "Aplicável a operações interestaduais destinadas a Goiás",
        },
    ]

    resultados = analisar_lote(itens_exemplo, tipi_exemplo, beneficios_exemplo, tabela_monofasico_exemplo, tabela_st_exemplo)
    for r in resultados:
        print("-" * 80)
        print(f"Produto: {r.descricao_produto}")
        print(f"NCM: {r.ncm} | Válido na TIPI: {r.ncm_valido}")
        print(f"Tem benefício ICMS: {r.tem_beneficio_icms} | Tem benefício PIS/COFINS: {r.tem_beneficio_piscofins}")
        print(f"Regime monofásico: {r.regime_monofasico}")
        print(f"Sujeito a ST-ICMS: {r.sujeito_st_icms} | Detalhe: {r.detalhe_st}")
        print(f"Análise: {r.analise}")
        if r.alerta:
            print(f"ALERTA: {r.alerta}")
