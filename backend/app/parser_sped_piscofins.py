"""
Parser das Tabelas SPED 4.3.10 e 4.3.11 (Receita Federal).

Essas tabelas trazem o histórico completo de alíquotas de PIS/COFINS para
produtos sujeitos a tributação concentrada (monofásica) ou por pauta —
principalmente combustíveis, fármacos/perfumaria, veículos/autopeças e
bebidas frias.

Cada linha tem um período de vigência (Início/Término de Escrituração).
Para a base de benefícios, só nos interessam as linhas VIGENTES
(sem data de término, ou com término no futuro) — linhas históricas
já revogadas não geram benefício aplicável hoje.

Duas categorias de interesse:
  1. Alíquota ZERO — é benefício de fato (isenção de PIS/COFINS)
  2. Alíquota monofásica específica — não é isenção, mas é um regime
     tributário diferenciado (concentração da tributação numa etapa),
     que precisa ser sinalizado mesmo não sendo "benefício" no sentido
     estrito, pois afeta como o produto deve ser tratado fiscalmente.

Uso:
    from parser_sped_piscofins import processar_tabela_sped

    registros = processar_tabela_sped("tabela_1.docx", origem="Tabela 4.3.11")
"""

import re
from datetime import date, datetime
from dataclasses import dataclass, asdict
from typing import Optional
import subprocess
import json


@dataclass
class RegistroSpedPisCofins:
    codigo: str
    descricao_produto: str
    ncm: Optional[str]              # pode ser None quando a tabela não traz NCM (ex: "Autopeças - Anexos I e II da Lei")
    ncm_bruto: str                  # texto original da coluna NCM, útil quando há múltiplos códigos/posições
    aliquota_pis: Optional[str]
    aliquota_cofins: Optional[str]
    aliquota_zero: bool             # true quando pis e cofins = 0,00
    vigencia_inicio: Optional[str]
    vigencia_fim: Optional[str]     # None = ainda vigente
    origem_tabela: str


def _extrair_tabela_markdown(caminho_docx: str) -> str:
    """Usa o extract-text (já disponível no ambiente) para pegar o docx em markdown."""
    resultado = subprocess.run(
        ["extract-text", caminho_docx],
        capture_output=True, text=True, check=True
    )
    return resultado.stdout


def _limpar_ncm(texto: str) -> Optional[str]:
    """
    Extrai o primeiro NCM completo de 8 dígitos de um texto que pode conter
    múltiplos códigos, posições (ex: '84.29'), ou texto livre.
    Retorna None se não achar nenhum padrão de NCM/posição reconhecível.
    """
    if not texto or texto.strip() in ("-", ""):
        return None

    # NCM completo: 0000.00.00 ou 00000000
    match_completo = re.search(r"\b(\d{4})\.?(\d{2})\.?(\d{2})\b", texto)
    if match_completo:
        return "".join(match_completo.groups())

    return None


def _extrair_todos_ncms(texto: str) -> list:
    """Extrai TODOS os NCMs completos de 8 dígitos mencionados na célula (pode ter vários)."""
    if not texto:
        return []
    matches = re.findall(r"\b(\d{4})\.?(\d{2})\.?(\d{2})\b", texto)
    return ["".join(m) for m in matches]


def _parsear_data(texto: str) -> Optional[str]:
    """Converte datas em formato DD/MM/AAAA ou MM/AAAA para ISO (AAAA-MM-DD)."""
    if not texto or texto.strip() in ("-", ""):
        return None
    texto = texto.strip().replace("*", "").strip()

    for fmt in ("%d/%m/%Y", "%m/%Y"):
        try:
            dt = datetime.strptime(texto, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _esta_vigente(vigencia_fim: Optional[str]) -> bool:
    """Sem data de término = vigente. Com data de término no futuro = ainda vigente."""
    if vigencia_fim is None:
        return True
    try:
        return date.fromisoformat(vigencia_fim) >= date.today()
    except ValueError:
        return True  # se não conseguir parsear, não descarta por segurança


def processar_tabela_sped(caminho_docx: str, origem: str) -> list:
    """
    Processa uma tabela SPED (4.3.10 ou 4.3.11) já convertida para .docx
    e retorna a lista de registros, já filtrada para conter só o que está
    vigente atualmente (sem data de término, ou com término futuro).
    """
    markdown = _extrair_tabela_markdown(caminho_docx)
    linhas_tabela = [l for l in markdown.split("\n") if l.strip().startswith("|")]

    registros = []
    codigo_atual = None
    descricao_atual = None

    for linha in linhas_tabela:
        celulas = [c.strip().replace("**", "") for c in linha.strip().strip("|").split("|")]

        # Pula linhas de separador markdown (---) e cabeçalhos
        if all(c in ("", "---") or set(c) <= {"-"} for c in celulas):
            continue

        # Heurística: linha de cabeçalho de seção (só 1-2 células preenchidas, resto vazio)
        # ex: "100 | COMBUSTÍVEIS E ÁLCOOL" -- pula, é só um título de grupo
        celulas_preenchidas = [c for c in celulas if c]
        if len(celulas_preenchidas) <= 2 and celulas[0].isdigit() and len(celulas[0]) == 3:
            continue

        if len(celulas) < 6:
            continue  # linha fora do formato esperado da tabela principal

        # As colunas variam um pouco entre a 4.3.10 e a 4.3.11, então localizamos
        # por conteúdo: código (3 dígitos numéricos), descrição, NCM, alíquotas, datas
        codigo = celulas[0] if celulas[0] else codigo_atual
        descricao = celulas[1] if celulas[1] else descricao_atual
        if not codigo or not descricao:
            continue
        codigo_atual, descricao_atual = codigo, descricao

        ncm_bruto = celulas[2] if len(celulas) > 2 else ""
        ncms_encontrados = _extrair_todos_ncms(ncm_bruto)

        # As duas últimas colunas numéricas antes das datas costumam ser PIS e COFINS;
        # localizamos por regex de número decimal nas células do meio
        valores_numericos = []
        for c in celulas[3:]:
            if re.match(r"^-?\d+[,.]\d+$", c):
                valores_numericos.append(c)

        aliquota_pis = valores_numericos[0] if len(valores_numericos) > 0 else None
        aliquota_cofins = valores_numericos[1] if len(valores_numericos) > 1 else None

        # Datas: procuram padrão DD/MM/AAAA ou MM/AAAA nas últimas células
        datas_encontradas = [c for c in celulas if re.match(r"^\d{2}/\d{4}$|^\d{2}/\d{2}/\d{4}$", c)]
        vigencia_inicio = _parsear_data(datas_encontradas[0]) if len(datas_encontradas) > 0 else None
        vigencia_fim = _parsear_data(datas_encontradas[1]) if len(datas_encontradas) > 1 else None

        aliquota_zero = False
        try:
            if aliquota_pis is not None and aliquota_cofins is not None:
                pis_f = float(aliquota_pis.replace(",", "."))
                cofins_f = float(aliquota_cofins.replace(",", "."))
                aliquota_zero = (pis_f == 0.0 and cofins_f == 0.0)
        except ValueError:
            pass

        # Só mantemos o que está vigente hoje
        if not _esta_vigente(vigencia_fim):
            continue

        if ncms_encontrados:
            for ncm in ncms_encontrados:
                registros.append(RegistroSpedPisCofins(
                    codigo=codigo, descricao_produto=descricao,
                    ncm=ncm, ncm_bruto=ncm_bruto,
                    aliquota_pis=aliquota_pis, aliquota_cofins=aliquota_cofins,
                    aliquota_zero=aliquota_zero,
                    vigencia_inicio=vigencia_inicio, vigencia_fim=vigencia_fim,
                    origem_tabela=origem,
                ))
        else:
            # Mantém mesmo sem NCM identificável (ex: regras por Anexo de Lei) —
            # fica sinalizado para revisão manual, não é descartado silenciosamente
            registros.append(RegistroSpedPisCofins(
                codigo=codigo, descricao_produto=descricao,
                ncm=None, ncm_bruto=ncm_bruto,
                aliquota_pis=aliquota_pis, aliquota_cofins=aliquota_cofins,
                aliquota_zero=aliquota_zero,
                vigencia_inicio=vigencia_inicio, vigencia_fim=vigencia_fim,
                origem_tabela=origem,
            ))

    return registros


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python parser_sped_piscofins.py caminho/tabela.docx \"Nome da Origem\"")
        sys.exit(1)

    registros = processar_tabela_sped(sys.argv[1], sys.argv[2])

    com_ncm = [r for r in registros if r.ncm]
    sem_ncm = [r for r in registros if not r.ncm]
    zero = [r for r in registros if r.aliquota_zero]

    print(f"Total de registros vigentes: {len(registros)}")
    print(f"Com NCM identificado: {len(com_ncm)}")
    print(f"Sem NCM identificado (revisar manualmente): {len(sem_ncm)}")
    print(f"Com alíquota ZERO (benefício): {len(zero)}")

    print("\n--- Exemplos com alíquota zero ---")
    for r in zero[:5]:
        print(asdict(r))

    print("\n--- Exemplos sem NCM (precisam de revisão) ---")
    for r in sem_ncm[:5]:
        print(f"{r.codigo} | {r.descricao_produto[:60]} | NCM bruto: '{r.ncm_bruto[:60]}'")
