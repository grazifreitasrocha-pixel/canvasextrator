"""
Extrator do Anexo IX do RCTE-GO (Regulamento do Código Tributário do
Estado de Goiás) — "Dos Benefícios Fiscais".

O anexo é dividido em artigos, cada um com uma lista de incisos em
algarismo romano. Os artigos relevantes para benefício por NCM são:

    Art. 6º  — isenções (sem prazo definido)
    Art. 7º  — isenções (com prazo de vigência)
    Art. 8º  — redução de base de cálculo (sem prazo)
    Art. 9º  — redução de base de cálculo (com prazo)
    Art. 11  — crédito outorgado

Cada inciso pode ter passado por várias redações ao longo dos anos
(histórico de alterações por decreto). Este extrator mantém sempre a
ÚLTIMA versão de cada inciso (a mais recente no arquivo == vigente),
e descarta os incisos marcados como "revogado".

Como nosso motor de cruzamento casa produtos por NCM, só temos como
aproveitar automaticamente os incisos que citam um código de
NCM/NBM explícito no texto — incisos baseados em tipo de operação,
natureza do contribuinte etc. (ex: "saída para exposição ou feira")
não são cruzáveis por NCM e ficam de fora desta extração automática
(mas continuam válidos juridicamente, só não entram na planilha).

Uso:
    from parser_anexo_ix import processar_anexo_ix

    beneficios = processar_anexo_ix("/caminho/anexo_ix.pdf")
"""

import re
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class IncisoBeneficio:
    artigo: str            # ex: "Art. 6º"
    inciso: str             # ex: "XXXVIII"
    tipo_beneficio: str    # isencao | reducao_base_calculo | credito_outorgado
    ncms: list              # lista de NCMs de 8 dígitos citados no texto
    texto: str               # texto completo do inciso (para condições/observações)
    referencia_legal: Optional[str]  # ex: "Convênio ICMS 51/94"
    precisa_revisao: bool = False    # true = quantidade de NCMs suspeita, conferir manualmente


# Mapa de qual tipo de benefício cada artigo representa
TIPO_POR_ARTIGO = {
    "Art. 6º": "isencao",
    "Art. 7º": "isencao",
    "Art. 8º": "reducao_base_calculo",
    "Art. 9º": "reducao_base_calculo",
    "Art. 11.": "credito_outorgado",
}

# Regex de um NCM/NBM: 4 dígitos + opcional .2 dígitos + opcional .2 dígitos
# (aceita formas parciais como posição "84.29" ou capítulo "84")
NCM_REGEX = re.compile(r"\b(\d{2}\.?\d{2}\.?\d{0,2}\.?\d{0,2})\b")


def _extrair_texto_pdf(caminho_pdf: str) -> str:
    resultado = subprocess.run(
        ["pdftotext", caminho_pdf, "-"],
        capture_output=True, text=True, check=True
    )
    return resultado.stdout


def _normalizar_ncm(bruto: str) -> Optional[str]:
    """
    Normaliza um código encontrado no texto para NCM de 8 dígitos quando
    possível. Retorna None se o código não tiver ao menos posição+subposição
    suficiente para ser útil (evita falso positivo tipo "art. 8º" virando NCM).
    """
    digitos = bruto.replace(".", "")
    if not digitos.isdigit():
        return None
    if len(digitos) == 8:
        return digitos
    if len(digitos) == 4:
        return digitos + "0000"  # posição completa, subposição fica genérica — marcar como prefixo depois
    return None


# Um inciso legislativo realista lista no máximo algumas dezenas de NCMs.
# Se a extração encontrar muito mais do que isso, é sinal de que o texto
# capturado ultrapassou os limites reais do inciso (ex: span colou com uma
# tabela/lista de outra natureza que aparece na sequência do documento) —
# nesses casos, marcamos para revisão manual em vez de confiar cegamente.
LIMITE_NCMS_POR_INCISO = 80

CAPITULOS_VALIDOS_NCM = {f"{i:02d}" for i in range(1, 98) if i != 77}


def _ncm_capitulo_valido(ncm: str) -> bool:
    """A NCM tem 97 capítulos possíveis (01 a 97, exceto o 77, reservado)."""
    return ncm[:2] in CAPITULOS_VALIDOS_NCM


def _extrair_ncms_do_texto(texto: str) -> list:
    """
    Extrai códigos NCM/NBM do texto de um inciso.

    Só executa a extração quando o inciso menciona "NCM" ou "NBM/SH" em
    algum lugar do texto (o inciso inteiro já é sobre um produto específico
    quando isso ocorre, então não precisamos de uma janela de contexto
    estreita ao redor de cada código — reduz falsos negativos por formatos
    de citação variados: "código X da NCM", "NCM X", "posição X da NCM/SH",
    "identificado pelos códigos da NCM/SH: X, Y, Z", etc.)

    Valida o capítulo (2 primeiros dígitos) contra a faixa real de capítulos
    da NCM, o que descarta números de 8 dígitos que não são NCM de verdade
    (números de processo, protocolo etc., que o regex solto poderia capturar).
    """
    if not re.search(r"\bNCM\b|\bNBM/SH\b|\bNBM\b", texto):
        return []

    ncms = set()

    # Formato completo com pontos: 0000.00.00 ou 00.00.00 ou 0000.00
    for m in re.finditer(r"\b(\d{2,4})\.(\d{2})\.(\d{2})\b", texto):
        codigo = "".join(m.groups())
        if len(codigo) == 8 and _ncm_capitulo_valido(codigo):
            ncms.add(codigo)
        elif len(codigo) == 6 and _ncm_capitulo_valido(codigo):
            ncms.add(codigo + "00")  # posição.subposição sem o último par — completa com 00

    # Formato completo sem pontos: 8 dígitos seguidos.
    # Exclui quando vier seguido de "/" — é começo de CNPJ (00000000/0001-00),
    # não NCM, e listas de empresas beneficiárias citam CNPJ nesse formato.
    for m in re.finditer(r"\b(\d{8})\b(?!\s*/)", texto):
        if _ncm_capitulo_valido(m.group(1)):
            ncms.add(m.group(1))

    # Posição isolada (4 dígitos com 1 ponto, ex: 87.16), só aceita quando
    # vier acompanhada da palavra "posição" logo antes, pra evitar capturar
    # números de convênio/decreto/ano por engano
    for m in re.finditer(r"posi[çc][ãa]o\s+(\d{2})\.(\d{2})\b", texto, re.IGNORECASE):
        codigo_prefixo = m.group(1) + m.group(2)
        if _ncm_capitulo_valido(codigo_prefixo):
            ncms.add(codigo_prefixo + "0000")  # marca como prefixo (4 dígitos + zeros)

    return sorted(ncms)


def _extrair_referencia_legal(texto: str) -> Optional[str]:
    """Pega a primeira referência tipo '(Convênio ICMS 51/94...)' do texto."""
    match = re.search(r"\(([^()]*(?:Convênio|Protocolo|Lei)[^()]*)\)", texto)
    if match:
        # corta em vírgula pra não trazer a cláusula inteira, só a norma
        return match.group(1).split(",")[0].strip()
    return None


def _dividir_em_incisos(texto_artigo: str, nome_artigo: str) -> dict:
    """
    Divide o texto de um artigo em incisos (I, II, III, ...), mantendo
    sempre a última ocorrência de cada numeral (redação mais recente).
    Descarta incisos cujo texto seja só "revogado".
    """
    # Cada inciso começa com o numeral romano seguido de " - " ou ". " no
    # início de uma linha (às vezes há indentação/quebra, então ancoramos
    # no início da linha lógica após normalizar quebras simples)
    padrao_item = re.compile(
        r"^([IVXLC]+(?:-[A-Z])?)\s*-\s*(.+?)(?=^\s*[IVXLC]+(?:-[A-Z])?\s*-\s*|\Z)",
        re.MULTILINE | re.DOTALL
    )

    incisos_texto = {}  # numeral -> texto mais recente (sobrescreve)
    for match in padrao_item.finditer(texto_artigo):
        numeral, corpo = match.group(1), match.group(2).strip()
        corpo_limpo = re.sub(r"\s+", " ", corpo)
        if not corpo_limpo:
            continue
        incisos_texto[numeral] = corpo_limpo  # última ocorrência vence

    return incisos_texto


def processar_anexo_ix(caminho_pdf: str) -> list:
    """
    Processa o Anexo IX completo e retorna a lista de IncisoBeneficio
    para os incisos que citam NCM/NBM explicitamente.
    """
    texto_completo = _extrair_texto_pdf(caminho_pdf)

    # Localiza os limites de cada artigo de interesse
    marcadores = list(TIPO_POR_ARTIGO.keys())
    posicoes = {}
    for marcador in marcadores:
        # ^Art. 6º (não pode casar com "Art. 6º-A" de outro contexto por engano)
        padrao = re.compile(r"^" + re.escape(marcador) + r"\s", re.MULTILINE)
        m = padrao.search(texto_completo)
        if m:
            posicoes[marcador] = m.start()

    ordenados = sorted(posicoes.items(), key=lambda kv: kv[1])

    resultados = []
    for i, (artigo, inicio) in enumerate(ordenados):
        fim = ordenados[i + 1][1] if i + 1 < len(ordenados) else len(texto_completo)
        trecho_artigo = texto_completo[inicio:fim]

        incisos = _dividir_em_incisos(trecho_artigo, artigo)
        tipo_beneficio = TIPO_POR_ARTIGO[artigo]

        for numeral, texto_inciso in incisos.items():
            if re.match(r"^revogad[oa]\.?\s*$", texto_inciso, re.IGNORECASE):
                continue  # inciso revogado, não gera benefício vigente

            # Alguns incisos concentram várias redações históricas no mesmo bloco
            # (quando a nota de revogação não inicia uma linha nova com o numeral).
            # Se o texto termina com uma marca de revogação para este mesmo
            # numeral, o inciso está revogado hoje — descarta mesmo que NCMs
            # antigos apareçam no meio do texto histórico.
            padrao_revogado_final = re.compile(
                re.escape(numeral) + r"\s*[-–—]\s*revogad[oa]\.?\s*;?\s*$", re.IGNORECASE
            )
            if padrao_revogado_final.search(texto_inciso.strip()):
                continue

            ncms = _extrair_ncms_do_texto(texto_inciso)
            if not ncms:
                continue  # sem NCM identificável — fora do escopo cruzável automaticamente

            resultados.append(IncisoBeneficio(
                artigo=artigo,
                inciso=numeral,
                tipo_beneficio=tipo_beneficio,
                ncms=ncms,
                texto=texto_inciso[:500],  # trunca pra não inflar demais a base
                referencia_legal=_extrair_referencia_legal(texto_inciso),
                precisa_revisao=len(ncms) > LIMITE_NCMS_POR_INCISO,
            ))

    return resultados


def converter_para_formato_motor(incisos: list) -> list:
    """
    Converte a lista de IncisoBeneficio para o formato que o
    motor_analise.py espera em `beneficios_cadastrados`.
    Um inciso com múltiplos NCMs vira múltiplos registros (um por NCM).

    Incisos marcados como `precisa_revisao=True` são pulados aqui —
    entram na base só depois de alguém conferir manualmente se a
    extração delimitou o texto do inciso corretamente.
    """
    registros = []
    for inc in incisos:
        if inc.precisa_revisao:
            continue

        norma_titulo = f"Anexo IX RCTE-GO, {inc.artigo} inciso {inc.inciso}"
        if inc.referencia_legal:
            norma_titulo += f" ({inc.referencia_legal})"

        for ncm in inc.ncms:
            eh_prefixo = ncm.endswith("0000") and len(ncm) == 8  # heurística: veio de posição de 4 dígitos
            registros.append({
                "ncm": ncm[:4] if eh_prefixo else ncm,
                "ncm_prefixo": eh_prefixo,
                "norma_titulo": norma_titulo,
                "tributo": "ICMS",
                "tipo_beneficio": inc.tipo_beneficio,
                "condicoes": inc.texto[:300],
                "vigencia_fim": None,
            })
    return registros


if __name__ == "__main__":
    import sys
    import json

    caminho = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/1787443365728_anexo_ix.pdf"

    incisos = processar_anexo_ix(caminho)
    revisao = [i for i in incisos if i.precisa_revisao]
    ok = [i for i in incisos if not i.precisa_revisao]

    print(f"Total de incisos com NCM identificado: {len(incisos)}")
    print(f"  -> prontos para uso automático: {len(ok)}")
    print(f"  -> marcados para revisão manual (volume suspeito de NCMs): {len(revisao)}")

    por_artigo = {}
    for inc in ok:
        por_artigo.setdefault(inc.artigo, 0)
        por_artigo[inc.artigo] += 1
    print("Por artigo (só os prontos para uso):", por_artigo)

    if revisao:
        print("\n--- Incisos que precisam de revisão manual ---")
        for inc in revisao:
            print(f"{inc.artigo} inciso {inc.inciso}: {len(inc.ncms)} NCMs encontrados (acima do limite de {LIMITE_NCMS_POR_INCISO})")

    print("\n--- Amostra dos prontos para uso (5 primeiros) ---")
    for inc in ok[:5]:
        print(json.dumps(asdict(inc), indent=2, ensure_ascii=False))
