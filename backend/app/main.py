"""
API do Sistema de Análise Fiscal (NCM x TIPI x Benefícios + Simples Nacional).
"""

import os
import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Text, ForeignKey, Boolean, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Session
import bcrypt
from jose import jwt, JWTError
from pydantic import BaseModel

from parser_nfe import processar_zip_nfe, extrair_itens_flat
from motor_analise import analisar_lote
from gerar_planilha import gerar_planilha_resultado
from simples_nacional import apurar_das_do_mes

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sistema_fiscal.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
ALGORITHM = "HS256"
TOKEN_EXPIRA_MINUTOS = 60 * 24 * 7

PASTA_BASES = Path(__file__).parent / "bases"
PASTA_ARQUIVOS = Path(__file__).parent / "arquivos"
PASTA_ARQUIVOS.mkdir(exist_ok=True)
PASTA_FRONTEND = Path(__file__).parent / "frontend"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha.encode("utf-8")[:72], senha_hash.encode("utf-8"))


# ---------------------------------------------------------------------------
# Modelos do banco
# ---------------------------------------------------------------------------

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    nome = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    senha_hash = Column(String, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)


class Lote(Base):
    __tablename__ = "lotes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    usuario_id = Column(String, ForeignKey("usuarios.id"), nullable=False)
    nome_arquivo = Column(String)
    status = Column(String, default="processando")
    total_itens = Column(Integer, default=0)
    total_com_beneficio_icms = Column(Integer, default=0)
    total_com_beneficio_piscofins = Column(Integer, default=0)
    total_alertas = Column(Integer, default=0)
    mensagem_erro = Column(Text, nullable=True)
    caminho_planilha = Column(String, nullable=True)
    rbt12_usado = Column(Float, nullable=True)
    anexo_usado = Column(String, nullable=True)
    das_total = Column(Float, nullable=True)
    aviso_pgdas = Column(Text, nullable=True)   # ex: "PGDAS enviado mas RBT12 não pôde ser extraído"
    criado_em = Column(DateTime, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)


Base.metadata.create_all(bind=engine)


def _migrar_colunas_faltantes():
    """
    Base.metadata.create_all() só cria tabelas que ainda não existem —
    ele NÃO adiciona colunas novas a tabelas já existentes. Como o banco
    de produção foi criado antes de campos como rbt12_usado/anexo_usado/
    das_total existirem no modelo, essa função confere e adiciona
    qualquer coluna que esteja faltando, evitando erro 500 silencioso
    no primeiro INSERT/UPDATE que tentar usar um campo inexistente.
    """
    from sqlalchemy import inspect, text

    inspetor = inspect(engine)
    if "lotes" not in inspetor.get_table_names():
        return  # tabela ainda nem existe, create_all já cuidou dela certinha

    colunas_existentes = {c["name"] for c in inspetor.get_columns("lotes")}

    colunas_esperadas = {
        "rbt12_usado": "DOUBLE PRECISION",
        "anexo_usado": "VARCHAR",
        "das_total": "DOUBLE PRECISION",
        "aviso_pgdas": "TEXT",
    }

    with engine.connect() as conexao:
        for nome_coluna, tipo_sql in colunas_esperadas.items():
            if nome_coluna not in colunas_existentes:
                print(f"[migração] adicionando coluna faltante: lotes.{nome_coluna} ({tipo_sql})")
                conexao.execute(text(f"ALTER TABLE lotes ADD COLUMN {nome_coluna} {tipo_sql}"))
                conexao.commit()


try:
    _migrar_colunas_faltantes()
except Exception as e:
    print(f"[migração] AVISO: falha ao migrar colunas (pode ser normal em SQLite local): {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def criar_token(usuario_id: str) -> str:
    expira = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRA_MINUTOS)
    return jwt.encode({"sub": usuario_id, "exp": expira}, SECRET_KEY, algorithm=ALGORITHM)


def usuario_atual(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Usuario:
    erro_credenciais = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não foi possível validar as credenciais",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        usuario_id = payload.get("sub")
        if usuario_id is None:
            raise erro_credenciais
    except JWTError:
        raise erro_credenciais

    usuario = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if usuario is None:
        raise erro_credenciais
    return usuario


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegistrarRequest(BaseModel):
    nome: str
    email: str
    senha: str


class SimplesNacionalRequest(BaseModel):
    rbt12: float
    anexo: str = "I"


class LoteResumo(BaseModel):
    id: str
    nome_arquivo: str
    status: str
    total_itens: int
    total_com_beneficio_icms: int
    total_com_beneficio_piscofins: int
    total_alertas: int
    mensagem_erro: Optional[str] = None
    rbt12_usado: Optional[float] = None
    anexo_usado: Optional[str] = None
    das_total: Optional[float] = None
    aviso_pgdas: Optional[str] = None
    criado_em: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Sistema de Análise Fiscal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def carregar_bases():
    def _carregar(nome_arquivo):
        caminho = PASTA_BASES / nome_arquivo
        if not caminho.exists():
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


BASES_CARREGADAS = carregar_bases()


@app.get("/api/status")
def status_api():
    tipo_banco = "postgresql" if "postgresql" in DATABASE_URL else "sqlite (temporário — reseta a cada deploy)"
    return {
        "status": "ok",
        "servico": "Sistema de Análise Fiscal",
        "banco_de_dados": tipo_banco,
    }


@app.get("/")
def raiz():
    caminho_index = PASTA_FRONTEND / "index.html"
    if caminho_index.exists():
        return FileResponse(caminho_index)
    return {"status": "ok", "servico": "Sistema de Análise Fiscal (frontend não encontrado)"}


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

@app.post("/auth/registrar", status_code=201)
def registrar(dados: RegistrarRequest, db: Session = Depends(get_db)):
    existe = db.query(Usuario).filter(Usuario.email == dados.email).first()
    if existe:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")

    usuario = Usuario(
        nome=dados.nome,
        email=dados.email,
        senha_hash=hash_senha(dados.senha),
    )
    db.add(usuario)
    db.commit()
    return {"mensagem": "Usuário criado com sucesso"}


@app.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.email == form.username).first()
    if not usuario or not verificar_senha(form.password, usuario.senha_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos")

    token = criar_token(usuario.id)
    return {"access_token": token, "token_type": "bearer", "nome": usuario.nome}


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------

def _processar_lote_em_background(lote_id: str, caminho_zip: str, rbt12: Optional[float] = None, anexo: Optional[str] = None):
    import traceback

    print(f"[lote {lote_id}] iniciando processamento de {caminho_zip}")
    db = SessionLocal()
    try:
        lote = db.query(Lote).filter(Lote.id == lote_id).first()
        if not lote:
            print(f"[lote {lote_id}] ERRO: registro do lote não encontrado no banco")
            return

        print(f"[lote {lote_id}] lendo ZIP...")
        resultado_zip = processar_zip_nfe(caminho_zip)
        print(f"[lote {lote_id}] ZIP lido: {resultado_zip['total_arquivos']} arquivos, "
              f"{resultado_zip['total_processados']} processados, "
              f"{resultado_zip['total_erros']} com erro")

        itens = extrair_itens_flat(resultado_zip)
        print(f"[lote {lote_id}] itens extraídos: {len(itens)}")

        if not itens:
            extensoes = resultado_zip.get("extensoes_encontradas", {})
            if resultado_zip["total_arquivos"] == 0 and extensoes:
                resumo_ext = ", ".join(f"{qtd}x .{ext}" for ext, qtd in sorted(extensoes.items(), key=lambda x: -x[1]))
                detalhe = f"Nenhum arquivo .xml encontrado no ZIP. Foram encontrados: {resumo_ext}."
            elif resultado_zip["total_arquivos"] == 0:
                detalhe = "O ZIP está vazio ou não pôde ser lido."
            elif resultado_zip["erros"]:
                detalhe = f"Erro ao processar os XMLs. Primeiro erro: {resultado_zip['erros'][0]['erro']}"
            else:
                detalhe = "Nenhum item de produto foi encontrado nas notas."

            print(f"[lote {lote_id}] finalizando com erro: {detalhe}")
            lote.status = "erro"
            lote.mensagem_erro = detalhe
            db.commit()
            return

        print(f"[lote {lote_id}] cruzando contra as bases...")
        beneficios_cadastrados = BASES_CARREGADAS["beneficios_icms"] + BASES_CARREGADAS["beneficios_piscofins"]
        resultados = analisar_lote(
            itens, BASES_CARREGADAS["tipi"], beneficios_cadastrados,
            BASES_CARREGADAS["monofasico"], BASES_CARREGADAS["substituicao_tributaria"],
        )

        apuracao_simples = None
        if rbt12 and anexo:
            print(f"[lote {lote_id}] apurando Simples Nacional (RBT12={rbt12}, Anexo={anexo})...")
            try:
                apuracao_simples = apurar_das_do_mes(resultados, rbt12=rbt12, anexo=anexo)
            except Exception as e:
                print(f"[lote {lote_id}] AVISO: falha na apuração do Simples ({e})")

        print(f"[lote {lote_id}] gerando planilha...")
        caminho_planilha = str(PASTA_ARQUIVOS / f"{lote_id}.xlsx")
        gerar_planilha_resultado(resultados, caminho_planilha, apuracao_simples=apuracao_simples)

        lote.status = "concluido"
        lote.total_itens = len(resultados)
        lote.total_com_beneficio_icms = sum(1 for r in resultados if r.tem_beneficio_icms)
        lote.total_com_beneficio_piscofins = sum(1 for r in resultados if r.tem_beneficio_piscofins)
        lote.total_alertas = sum(1 for r in resultados if r.alerta)
        lote.caminho_planilha = caminho_planilha
        lote.rbt12_usado = rbt12
        lote.anexo_usado = anexo
        lote.das_total = apuracao_simples.das_total if apuracao_simples else None
        lote.concluido_em = datetime.utcnow()
        db.commit()
        print(f"[lote {lote_id}] concluído com sucesso: {len(resultados)} itens")
    except Exception as e:
        erro_completo = traceback.format_exc()
        print(f"[lote {lote_id}] EXCEÇÃO NÃO TRATADA:\n{erro_completo}")
        lote = db.query(Lote).filter(Lote.id == lote_id).first()
        if lote:
            lote.status = "erro"
            lote.mensagem_erro = f"{type(e).__name__}: {e}"
            db.commit()
    finally:
        db.close()


@app.post("/lotes/upload", response_model=LoteResumo)
def upload_lote(
    background_tasks: BackgroundTasks,
    arquivo: UploadFile = File(...),
    pgdas: Optional[UploadFile] = File(None),
    rbt12: Optional[float] = Form(None),
    anexo: Optional[str] = Form(None),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
):
    if not arquivo.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .zip contendo os XMLs de NF-e.")

    lote = Lote(usuario_id=usuario.id, nome_arquivo=arquivo.filename, status="processando")
    db.add(lote)
    db.commit()
    db.refresh(lote)

    caminho_zip = str(PASTA_ARQUIVOS / f"{lote.id}_origem.zip")
    with open(caminho_zip, "wb") as f:
        shutil.copyfileobj(arquivo.file, f)

    # Se o usuário subiu o PDF do PGDAS-D, extrai o RBT12 automaticamente
    # dele — tem prioridade sobre um valor de RBT12 digitado manualmente.
    rbt12_final = rbt12
    aviso_pgdas = None
    if pgdas is not None and pgdas.filename:
        caminho_pgdas = str(PASTA_ARQUIVOS / f"{lote.id}_pgdas.pdf")
        with open(caminho_pgdas, "wb") as f:
            shutil.copyfileobj(pgdas.file, f)
        try:
            from parser_pgdas import extrair_dados_pgdas
            dados_pgdas = extrair_dados_pgdas(caminho_pgdas)
            if dados_pgdas.rbt12:
                rbt12_final = dados_pgdas.rbt12
                print(f"[lote {lote.id}] RBT12 extraído do PGDAS: R$ {rbt12_final:,.2f}")
            else:
                aviso_pgdas = (
                    "O PDF do PGDAS-D foi recebido, mas não foi possível localizar o "
                    "valor do RBT12 nele — o formato deste PDF pode ser diferente do "
                    "esperado. O Simples Nacional NÃO foi calculado para este lote "
                    "(a não ser que você também tenha digitado o RBT12 manualmente)."
                )
                print(f"[lote {lote.id}] AVISO: não foi possível extrair RBT12 do PGDAS enviado.")
        except Exception as e:
            aviso_pgdas = f"Erro ao processar o PDF do PGDAS-D enviado: {e}"
            print(f"[lote {lote.id}] ERRO ao processar PGDAS: {e}")

    if not anexo:
        anexo = "I"  # Comércio é o padrão mais comum; ajustável via parâmetro

    lote.aviso_pgdas = aviso_pgdas
    db.commit()

    background_tasks.add_task(_processar_lote_em_background, lote.id, caminho_zip, rbt12_final, anexo)

    return lote


@app.get("/lotes", response_model=list[LoteResumo])
def listar_lotes(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    return (
        db.query(Lote)
        .filter(Lote.usuario_id == usuario.id)
        .order_by(Lote.criado_em.desc())
        .all()
    )


@app.get("/lotes/{lote_id}", response_model=LoteResumo)
def detalhe_lote(lote_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    lote = db.query(Lote).filter(Lote.id == lote_id, Lote.usuario_id == usuario.id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
    return lote


@app.get("/lotes/{lote_id}/download")
def download_lote(lote_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    lote = db.query(Lote).filter(Lote.id == lote_id, Lote.usuario_id == usuario.id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
    if lote.status != "concluido" or not lote.caminho_planilha:
        raise HTTPException(status_code=400, detail="Lote ainda não foi concluído ou não gerou planilha.")
    if not os.path.exists(lote.caminho_planilha):
        raise HTTPException(status_code=404, detail="Arquivo da planilha não encontrado no servidor.")

    nome_download = f"analise_{lote.nome_arquivo.replace('.zip', '')}.xlsx"
    return FileResponse(
        lote.caminho_planilha,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=nome_download,
    )
