"""
Validação de classificação fiscal via IA (Claude).

Para cada item extraído da nota fiscal, este módulo pergunta ao modelo:
"a descrição do produto é compatível com o NCM/descrição TIPI aplicados?"

Requer a variável de ambiente ANTHROPIC_API_KEY configurada no servidor
onde este módulo rodar.

Uso:
    from classificador_ia import validar_lote_com_ia

    resultados = validar_lote_com_ia(itens)  # itens vêm do motor_analise
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Optional
import urllib.request
import urllib.error

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODELO = "claude-sonnet-4-6"
TAMANHO_LOTE = 15


@dataclass
class ParecerIA:
    ncm: str
    descricao_produto: str
    classificacao_coerente: bool
    nivel_confianca: str
    justificativa: str
    ncm_sugerido: Optional[str] = None


PROMPT_SISTEMA = """Você é um especialista em classificação fiscal de mercadorias (NCM/TIPI) no Brasil.

Você vai receber uma lista de itens de notas fiscais, cada um com:
- descrição do produto (como consta na nota fiscal, geralmente abreviada)
- NCM aplicado na nota
- descrição oficial da TIPI para esse NCM (código de 8 dígitos)

Sua tarefa é avaliar, para CADA item, se a descrição do produto é COERENTE com a
descrição oficial da TIPI para o NCM aplicado.

Responda APENAS com um JSON válido (sem markdown, sem texto antes ou depois),
no formato de uma lista de objetos:

[
  {
    "indice": 0,
    "classificacao_coerente": true,
    "nivel_confianca": "alta",
    "justificativa": "Texto curto explicando o porquê, em português.",
    "ncm_sugerido": null
  }
]
"""


def _montar_prompt_usuario(itens_lote: list) -> str:
    linhas = []
    for idx, item in enumerate(itens_lote):
        linhas.append(
            f"{idx}. Descrição na nota: \"{item['descricao_produto']}\"\n"
            f"   NCM aplicado: {item['ncm']}\n"
            f"   Descrição TIPI para esse NCM: \"{item.get('tipi_descricao') or 'NÃO ENCONTRADA NA TIPI'}\""
        )
    return "Analise os seguintes itens:\n\n" + "\n\n".join(linhas)


def _chamar_claude(prompt_usuario: str, api_key: str) -> str:
    corpo = json.dumps({
        "model": MODELO,
        "max_tokens": 4000,
        "system": PROMPT_SISTEMA,
        "messages": [{"role": "user", "content": prompt_usuario}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode("utf-8")
        raise RuntimeError(f"Erro na API Anthropic ({e.code}): {corpo_erro}")

    blocos_texto = [b["text"] for b in dados.get("content", []) if b.get("type") == "text"]
    return "\n".join(blocos_texto)


def _parsear_resposta_ia(texto_resposta: str) -> list:
    texto_limpo = texto_resposta.strip()
    if texto_limpo.startswith("```"):
        texto_limpo = texto_limpo.split("```")[1]
        if texto_limpo.startswith("json"):
            texto_limpo = texto_limpo[4:]
    return json.loads(texto_limpo.strip())


def validar_lote_com_ia(itens: list, api_key: Optional[str] = None, pausa_entre_chamadas: float = 0.5) -> list:
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada.")

    pareceres_por_indice_global = {}

    for inicio in range(0, len(itens), TAMANHO_LOTE):
        sublote = itens[inicio:inicio + TAMANHO_LOTE]
        prompt_usuario = _montar_prompt_usuario(sublote)

        texto_resposta = _chamar_claude(prompt_usuario, api_key)
        pareceres_brutos = _parsear_resposta_ia(texto_resposta)

        for p in pareceres_brutos:
            idx_local = p["indice"]
            idx_global = inicio + idx_local
            item = sublote[idx_local]
            pareceres_por_indice_global[idx_global] = ParecerIA(
                ncm=item["ncm"],
                descricao_produto=item["descricao_produto"],
                classificacao_coerente=p["classificacao_coerente"],
                nivel_confianca=p["nivel_confianca"],
                justificativa=p["justificativa"],
                ncm_sugerido=p.get("ncm_sugerido"),
            )

        if inicio + TAMANHO_LOTE < len(itens):
            time.sleep(pausa_entre_chamadas)

    resultado_final = []
    for idx, item in enumerate(itens):
        if idx in pareceres_por_indice_global:
            resultado_final.append(pareceres_por_indice_global[idx])
        else:
            resultado_final.append(ParecerIA(
                ncm=item["ncm"],
                descricao_produto=item["descricao_produto"],
                classificacao_coerente=True,
                nivel_confianca="baixa",
                justificativa="Não foi possível obter parecer da IA para este item.",
            ))

    return resultado_final


def montar_dicionario_pareceres(pareceres: list) -> dict:
    """Converte a lista de ParecerIA num dict indexado por 'ncm|descricao_produto'."""
    return {f"{p.ncm}|{p.descricao_produto}": p for p in pareceres}


if __name__ == "__main__":
    itens_teste = [
        {"descricao_produto": "PARAFUSO DE ACO INOX M6", "ncm": "73181500",
         "tipi_descricao": "Parafusos de ferro fundido, ferro ou aço"},
    ]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY não configurada neste ambiente de teste — pulando chamada real.")
    else:
        resultados = validar_lote_com_ia(itens_teste)
        for r in resultados:
            print(r)
