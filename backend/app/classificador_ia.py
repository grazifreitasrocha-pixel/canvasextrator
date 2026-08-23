"""
Validação de classificação fiscal via IA (Claude).

Para cada item extraído da nota fiscal, este módulo pergunta ao modelo:
"a descrição do produto é compatível com o NCM/descrição TIPI aplicados?"

Isso pega erros que o cruzamento puro de regras não pega — por exemplo,
uma nota que descreve "parafuso de aço" mas foi classificada com um NCM
de outro capítulo qualquer (erro de cadastro, item errado copiado de
outro produto, etc.), o que gera risco de autuação por classificação
incorreta.

Requer a variável de ambiente ANTHROPIC_API_KEY configurada no servidor
onde este módulo rodar (não é a chave do usuário final — é a chave do
sistema, usada só para essa análise de IA).

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

# Quantos itens processar por chamada (agrupar reduz custo e chamadas de rede,
# mas grupos grandes demais reduzem a qualidade da análise por item)
TAMANHO_LOTE = 15


@dataclass
class ParecerIA:
    ncm: str
    descricao_produto: str
    classificacao_coerente: bool  # True = faz sentido, False = suspeito
    nivel_confianca: str  # alta | media | baixa
    justificativa: str
    ncm_sugerido: Optional[str] = None  # se a IA sugerir outro NCM mais adequado


PROMPT_SISTEMA = """Você é um especialista em classificação fiscal de mercadorias (NCM/TIPI) no Brasil.

Você vai receber uma lista de itens de notas fiscais, cada um com:
- descrição do produto (como consta na nota fiscal, geralmente abreviada)
- NCM aplicado na nota
- descrição oficial da TIPI para esse NCM (código de 8 dígitos)

Sua tarefa é avaliar, para CADA item, se a descrição do produto é COERENTE com a
descrição oficial da TIPI para o NCM aplicado. Considere:

- Descrições de notas fiscais costumam ser abreviadas/em caixa alta e usar
  jargão comercial — isso é normal, não é por si só um problema.
- O problema real é quando a NATUREZA do produto descrito não bate com a
  natureza do que a TIPI descreve para aquele NCM (ex: nota descreve
  "parafuso" mas o NCM é de "medicamento").
- Leve em conta que o NCM tem 8 dígitos e granularidade fina — pequenas
  imprecisões de subcategoria não são necessariamente erro grave, mas
  categoria/capítulo completamente diferente é sinal forte de erro.
- Se não tiver certeza suficiente, marque nivel_confianca como "baixa" em vez
  de arriscar um veredito.

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

O campo "indice" deve corresponder à posição do item na lista recebida (começando em 0).
O campo "ncm_sugerido" só deve ser preenchido quando classificacao_coerente for false
E você tiver uma sugestão razoável de NCM mais adequado; caso contrário, use null.
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
    """Chama a API da Anthropic e retorna o texto de resposta bruto."""
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
    """Remove possíveis cercas de markdown e faz o parse do JSON."""
    texto_limpo = texto_resposta.strip()
    if texto_limpo.startswith("```"):
        texto_limpo = texto_limpo.split("```")[1]
        if texto_limpo.startswith("json"):
            texto_limpo = texto_limpo[4:]
    return json.loads(texto_limpo.strip())


def validar_lote_com_ia(itens: list, api_key: Optional[str] = None, pausa_entre_chamadas: float = 0.5) -> list:
    """
    Valida uma lista de itens (descrição + NCM + descrição TIPI) via IA.

    itens: lista de dicts com pelo menos:
        {"descricao_produto": ..., "ncm": ..., "tipi_descricao": ...}

    Retorna lista de ParecerIA, na mesma ordem dos itens de entrada.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não configurada. Defina a variável de ambiente "
            "no servidor antes de rodar a validação por IA."
        )

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
            time.sleep(pausa_entre_chamadas)  # evita estourar rate limit em lotes grandes

    # Garante que todo item tenha um parecer, mesmo que a IA tenha pulado algum
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
                justificativa="Não foi possível obter parecer da IA para este item (falha no processamento do lote).",
            ))

    return resultado_final


def montar_dicionario_pareceres(pareceres: list) -> dict:
    """
    Converte a lista de ParecerIA (ordem posicional) num dict indexado por
    "ncm|descricao_produto", que é o formato que gerar_planilha.py espera
    para casar cada parecer com a linha certa da planilha.
    """
    return {f"{p.ncm}|{p.descricao_produto}": p for p in pareceres}


if __name__ == "__main__":
    # Teste local. Requer ANTHROPIC_API_KEY configurada no ambiente.
    itens_teste = [
        {
            "descricao_produto": "PARAFUSO DE ACO INOX M6",
            "ncm": "73181500",
            "tipi_descricao": "Parafusos de ferro fundido, ferro ou aço",
        },
        {
            "descricao_produto": "MEDICAMENTO GENERICO XYZ 500MG",
            "ncm": "30049099",
            "tipi_descricao": "Outros medicamentos",
        },
        {
            "descricao_produto": "NOTEBOOK DELL I7 16GB",
            "ncm": "73181500",  # NCM propositalmente errado (é de parafuso) pra testar detecção
            "tipi_descricao": "Parafusos de ferro fundido, ferro ou aço",
        },
    ]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY não configurada neste ambiente de teste — pulando chamada real.")
        print("O módulo está pronto; basta configurar a variável de ambiente em produção.")
    else:
        resultados = validar_lote_com_ia(itens_teste)
        for r in resultados:
            print("-" * 80)
            print(f"Produto: {r.descricao_produto} | NCM: {r.ncm}")
            print(f"Coerente: {r.classificacao_coerente} | Confiança: {r.nivel_confianca}")
            print(f"Justificativa: {r.justificativa}")
            if r.ncm_sugerido:
                print(f"NCM sugerido: {r.ncm_sugerido}")
