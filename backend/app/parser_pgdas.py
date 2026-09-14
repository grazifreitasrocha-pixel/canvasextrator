"""
Extrator de dados do extrato em PDF do PGDAS-D (Programa Gerador do
Documento de Arrecadação do Simples Nacional).

O dado mais importante extraído é o RBT12 (Receita Bruta acumulada nos
últimos 12 meses), que alimenta o cálculo da alíquota efetiva do mês
seguinte (simples_nacional.py). Também extrai dados de identificação
para conferência (CNPJ, nome empresarial, período de apuração, UF).

Usa `pdftotext -layout`, que preserva a posição das colunas — sem isso,
os valores numéricos ficam desalinhados do rótulo da linha.

Uso:
    from parser_pgdas import extrair_dados_pgdas

    dados = extrair_dados_pgdas("/caminho/pgdas.pdf")
    print(dados.rbt12)
"""

import re
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class DadosPgdas:
    cnpj: Optional[str]
    nome_empresarial: Optional[str]
    periodo_apuracao_inicio: Optional[str]   # formato DD/MM/AAAA
    periodo_apuracao_fim: Optional[str]
    municipio: Optional[str]
    uf: Optional[str]
    rbt12: Optional[float]
    rpa_competencia: Optional[float]         # receita bruta do próprio mês declarado (para conferência)
    sublimite_receita_anual: Optional[float]


def _extrair_texto_layout(caminho_pdf: str) -> str:
    resultado = subprocess.run(
        ["pdftotext", "-layout", caminho_pdf, "-"],
        capture_output=True, text=True, check=True
    )
    return resultado.stdout


def _para_float(valor_str: str) -> Optional[float]:
    """Converte '2.648.440,53' (formato BR) para float."""
    if not valor_str:
        return None
    try:
        return float(valor_str.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _extrair_rbt12(texto: str) -> Optional[float]:
    """
    Localiza a linha "Receita bruta acumulada nos doze meses anteriores"
    que tem números na própria linha (a versão RBT12p/proporcionalizada
    fica sem números quando não se aplica, então não é capturada aqui).
    Pega o ÚLTIMO número da linha, que é a coluna "Total".
    """
    padrao = re.compile(
        r"Receita bruta acumulada nos doze meses anteriores\s+"
        r"([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)"
    )
    match = padrao.search(texto)
    if not match:
        return None
    return _para_float(match.group(3))  # coluna "Total"


def _extrair_rpa(texto: str) -> Optional[float]:
    """Receita Bruta do PA (RPA) - Competência — receita do próprio mês declarado."""
    padrao = re.compile(
        r"Receita Bruta do PA \(RPA\)[^\n]*?\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)"
    )
    match = padrao.search(texto)
    if not match:
        return None
    return _para_float(match.group(3))


def _extrair_periodo_apuracao(texto: str) -> tuple:
    match = re.search(r"Período de Apuração:\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})", texto)
    if match:
        return match.group(1), match.group(2)
    return None, None


def _extrair_campo_simples(texto: str, rotulo: str) -> Optional[str]:
    """Extrai o valor de um campo no formato 'Rótulo: valor' na mesma linha."""
    match = re.search(re.escape(rotulo) + r":\s*(.+)", texto)
    if match:
        return match.group(1).strip()
    return None


def _extrair_municipio_uf(texto: str) -> tuple:
    match = re.search(r"Município:\s*([A-ZÀ-Ú\s]+?)\s+UF:\s*([A-Z]{2})", texto)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, None


def _extrair_sublimite(texto: str) -> Optional[float]:
    match = re.search(r"Sublimite de Receita Anual \(R\$\):\s*([\d.,]+)", texto)
    if match:
        return _para_float(match.group(1))
    return None


def extrair_dados_pgdas(caminho_pdf: str) -> DadosPgdas:
    """Processa o extrato do PGDAS-D e retorna os dados relevantes para apuração."""
    texto = _extrair_texto_layout(caminho_pdf)

    cnpj = _extrair_campo_simples(texto, "CNPJ Matriz")
    nome = _extrair_campo_simples(texto, "Nome empresarial")
    inicio, fim = _extrair_periodo_apuracao(texto)
    municipio, uf = _extrair_municipio_uf(texto)
    rbt12 = _extrair_rbt12(texto)
    rpa = _extrair_rpa(texto)
    sublimite = _extrair_sublimite(texto)

    return DadosPgdas(
        cnpj=cnpj,
        nome_empresarial=nome,
        periodo_apuracao_inicio=inicio,
        periodo_apuracao_fim=fim,
        municipio=municipio,
        uf=uf,
        rbt12=rbt12,
        rpa_competencia=rpa,
        sublimite_receita_anual=sublimite,
    )


if __name__ == "__main__":
    import sys
    from dataclasses import asdict
    import json

    caminho = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/pgdas.pdf"
    dados = extrair_dados_pgdas(caminho)
    print(json.dumps(asdict(dados), indent=2, ensure_ascii=False))
