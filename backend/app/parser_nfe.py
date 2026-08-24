"""
Parser de XML de NF-e (layout 4.00).

Recebe um arquivo .zip contendo um ou mais XMLs de NF-e e extrai,
para cada item de cada nota: descrição do produto, NCM, CFOP, CST,
quantidade e valores.

Uso:
    from parser_nfe import processar_zip_nfe

    resultado = processar_zip_nfe("/caminho/notas.zip")
    for nota in resultado["notas"]:
        print(nota["chave_acesso"], nota["itens"])
"""

import zipfile
import io
from lxml import etree
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import date

# Namespace padrão da NF-e (layout 4.00)
NFE_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


@dataclass
class ItemNota:
    numero_item: int
    codigo_produto: Optional[str]
    descricao_produto: str
    ncm: str
    cest: Optional[str]
    cfop: Optional[str]
    cst: Optional[str]
    ean: Optional[str]
    quantidade: Optional[float]
    unidade: Optional[str]
    valor_unitario: Optional[float]
    valor_total: Optional[float]


@dataclass
class NotaFiscal:
    chave_acesso: Optional[str]
    numero_nf: Optional[str]
    data_emissao: Optional[str]
    cnpj_emitente: Optional[str]
    nome_emitente: Optional[str]
    uf_emitente: Optional[str]
    arquivo_origem: str
    itens: list = field(default_factory=list)
    erro: Optional[str] = None


def _texto(elemento, xpath, default=None):
    """Busca texto de um elemento via xpath, com fallback seguro."""
    achou = elemento.find(xpath, NFE_NS)
    if achou is not None and achou.text:
        return achou.text.strip()
    return default


def _numero(elemento, xpath):
    valor = _texto(elemento, xpath)
    if valor is None:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def parsear_xml_nfe(conteudo_xml: bytes, nome_arquivo: str) -> NotaFiscal:
    """Parseia um único XML de NF-e e retorna a nota com seus itens."""
    try:
        root = etree.fromstring(conteudo_xml)
    except etree.XMLSyntaxError as e:
        return NotaFiscal(
            chave_acesso=None, numero_nf=None, data_emissao=None,
            cnpj_emitente=None, nome_emitente=None, uf_emitente=None,
            arquivo_origem=nome_arquivo, erro=f"XML inválido: {e}"
        )

    # A infNFe pode estar na raiz ou dentro de <nfeProc>
    inf_nfe = root.find(".//nfe:infNFe", NFE_NS)
    if inf_nfe is None:
        return NotaFiscal(
            chave_acesso=None, numero_nf=None, data_emissao=None,
            cnpj_emitente=None, nome_emitente=None, uf_emitente=None,
            arquivo_origem=nome_arquivo, erro="Elemento <infNFe> não encontrado"
        )

    # Chave de acesso vem do atributo Id (formato: NFe + 44 dígitos)
    chave_bruta = inf_nfe.get("Id", "")
    chave_acesso = chave_bruta.replace("NFe", "") if chave_bruta else None

    ide = inf_nfe.find("nfe:ide", NFE_NS)
    emit = inf_nfe.find("nfe:emit", NFE_NS)

    numero_nf = _texto(ide, "nfe:nNF") if ide is not None else None
    data_emissao = _texto(ide, "nfe:dhEmi") if ide is not None else None
    if data_emissao:
        data_emissao = data_emissao[:10]  # pega só YYYY-MM-DD

    cnpj_emitente = _texto(emit, "nfe:CNPJ") if emit is not None else None
    nome_emitente = _texto(emit, "nfe:xNome") if emit is not None else None
    uf_emitente = None
    if emit is not None:
        ender_emit = emit.find("nfe:enderEmit", NFE_NS)
        if ender_emit is not None:
            uf_emitente = _texto(ender_emit, "nfe:UF")

    nota = NotaFiscal(
        chave_acesso=chave_acesso,
        numero_nf=numero_nf,
        data_emissao=data_emissao,
        cnpj_emitente=cnpj_emitente,
        nome_emitente=nome_emitente,
        uf_emitente=uf_emitente,
        arquivo_origem=nome_arquivo,
    )

    # Cada <det> é um item da nota
    for det in inf_nfe.findall("nfe:det", NFE_NS):
        numero_item = int(det.get("nItem", 0))
        prod = det.find("nfe:prod", NFE_NS)
        if prod is None:
            continue

        imposto = det.find("nfe:imposto", NFE_NS)
        cst = None
        if imposto is not None:
            # CST pode estar em ICMS/CSOSN dependendo do regime; tenta os mais comuns
            for caminho in [
                ".//nfe:ICMS//nfe:CST",
                ".//nfe:ICMS//nfe:CSOSN",
            ]:
                achou = imposto.find(caminho, NFE_NS)
                if achou is not None and achou.text:
                    cst = achou.text.strip()
                    break

        item = ItemNota(
            numero_item=numero_item,
            codigo_produto=_texto(prod, "nfe:cProd"),
            descricao_produto=_texto(prod, "nfe:xProd", default="(sem descrição)"),
            ncm=_texto(prod, "nfe:NCM", default=""),
            cest=_texto(prod, "nfe:CEST"),
            cfop=_texto(prod, "nfe:CFOP"),
            cst=cst,
            ean=_texto(prod, "nfe:cEAN"),
            quantidade=_numero(prod, "nfe:qCom"),
            unidade=_texto(prod, "nfe:uCom"),
            valor_unitario=_numero(prod, "nfe:vUnCom"),
            valor_total=_numero(prod, "nfe:vProd"),
        )
        nota.itens.append(item)

    return nota


def processar_zip_nfe(caminho_zip: str) -> dict:
    """
    Processa um arquivo ZIP contendo XMLs de NF-e.

    Retorna um dict com:
        - total_arquivos: quantidade de XMLs encontrados
        - notas: lista de NotaFiscal (como dict)
        - erros: lista de arquivos que falharam no parse
    """
    notas = []
    erros = []

    with zipfile.ZipFile(caminho_zip, "r") as zf:
        nomes_xml = [n for n in zf.namelist() if n.lower().endswith(".xml")]

        for nome in nomes_xml:
            try:
                conteudo = zf.read(nome)
                nota = parsear_xml_nfe(conteudo, nome)
                if nota.erro:
                    erros.append({"arquivo": nome, "erro": nota.erro})
                else:
                    notas.append(nota)
            except Exception as e:
                erros.append({"arquivo": nome, "erro": str(e)})

    return {
        "total_arquivos": len(nomes_xml),
        "total_processados": len(notas),
        "total_erros": len(erros),
        "notas": [asdict(n) for n in notas],
        "erros": erros,
    }


def extrair_itens_flat(resultado_processamento: dict) -> list:
    """
    Achata o resultado em uma lista simples de itens, útil pra gerar
    a planilha final direto (descrição + NCM + dados da nota de origem).
    """
    linhas = []
    for nota in resultado_processamento["notas"]:
        for item in nota["itens"]:
            linhas.append({
                "arquivo_origem": nota["arquivo_origem"],
                "chave_acesso": nota["chave_acesso"],
                "numero_nf": nota["numero_nf"],
                "data_emissao": nota["data_emissao"],
                "cnpj_emitente": nota["cnpj_emitente"],
                "nome_emitente": nota["nome_emitente"],
                "descricao_produto": item["descricao_produto"],
                "ncm": item["ncm"],
                "cest": item.get("cest"),
                "cfop": item["cfop"],
                "quantidade": item["quantidade"],
                "valor_total": item["valor_total"],
            })
    return linhas


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Uso: python parser_nfe.py caminho/para/notas.zip")
        sys.exit(1)

    resultado = processar_zip_nfe(sys.argv[1])
    print(f"Arquivos encontrados: {resultado['total_arquivos']}")
    print(f"Processados com sucesso: {resultado['total_processados']}")
    print(f"Erros: {resultado['total_erros']}")

    itens = extrair_itens_flat(resultado)
    print(f"\nTotal de itens extraídos: {len(itens)}")
    if itens:
        print("\nExemplo do primeiro item:")
        print(json.dumps(itens[0], indent=2, ensure_ascii=False))
