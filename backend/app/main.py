"""
API do Sistema de Análise Fiscal (NCM x TIPI x Benefícios).

Endpoints principais:
    POST /auth/registrar       — cria usuário
    POST /auth/login           — autentica e devolve token
    POST /lotes/upload         — envia ZIP de NF-e, processa, guarda resultado
    GET  /lotes                — lista lotes do usuário logado
    GET  /lotes/{id}           — detalhe/resumo de um lote
    GET  /lotes/{id}/download  — baixa a planilha Excel do resultado

Rodar localmente:
    uvicorn main:app --reload
"""

import os
import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Text, ForeignKey, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, Session
import bcrypt
from jose import jwt, JWTError
from pydantic import BaseModel

from parser_nfe import processar_zip_nfe, extrair_itens_flat
from motor_analise import analisar_lote
from gerar_planilha import gerar_planilha_resultado

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sistema_fiscal.db")
# Railway (e outros provedores) fornecem a URL como "postgres://" ou
# "postgresql://", mas para usar o driver psycopg (v3, sem compilação
# nativa — mais confiável em ambientes de build tipo Railway/Nixpacks)
# precisamos do prefixo explícito "postgresql+psycopg://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
ALGORITHM = "HS256"
TOKEN_EXPIRA_MINUTOS = 60 * 24 * 7  # 7 dias

PASTA_BASES = Path(__file__).parent / "bases"
PASTA_ARQUIVOS = Path(__file__).parent / "arquivos"
PASTA_ARQUIVOS.mkdir(exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

pwd_context = None  # não usamos mais passlib — funções de hash abaixo usam bcrypt diretamente
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
    status = Column(String, default="processando")  # processando | concluido | erro
    total_itens = Column(Integer, default=0)
    total_com_beneficio_icms = Column(Integer, default=0)
    total_com_beneficio_piscofins = Column(Integer, default=0)
    total_alertas = Column(Integer, default=0)
    mensagem_erro = Column(Text, nullable=True)
    caminho_planilha = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)


Base.metadata.create_all(bind=engine)


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
# Schemas (Pydantic)
# ---------------------------------------------------------------------------

class RegistrarRequest(BaseModel):
    nome: str
    email: str
    senha: str


class LoteResumo(BaseModel):
    id: str
    nome_arquivo: str
    status: str
    total_itens: int
    total_com_beneficio_icms: int
    total_com_beneficio_piscofins: int
    total_alertas: int
    mensagem_erro: Optional[str] = None
    criado_em: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Sistema de Análise Fiscal")

app.add_middleware(
    CORSMiddleware,
    # Em produção, defina ALLOWED_ORIGINS no ambiente com a URL real do
    # frontend (ex: "https://seu-app.vercel.app"), separadas por vírgula
    # se houver mais de uma. Sem essa variável, libera geral (útil só
    # para desenvolvimento local).
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


# carrega uma vez na subida do servidor (as bases não mudam a cada request)
BASES_CARREGADAS = carregar_bases()


PASTA_FRONTEND = Path(__file__).parent / "frontend"


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
# Lotes (upload, processamento, listagem, download)
# ---------------------------------------------------------------------------

def _processar_lote_em_background(lote_id: str, caminho_zip: str):
    """Roda o pipeline de processamento e atualiza o registro do lote no banco."""
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
        if resultado_zip["erros"]:
            for e in resultado_zip["erros"][:5]:
                print(f"[lote {lote_id}]   erro no arquivo {e['arquivo']}: {e['erro']}")

        itens = extrair_itens_flat(resultado_zip)
        print(f"[lote {lote_id}] itens extraídos: {len(itens)}")

        if not itens:
            detalhe = (
                f"ZIP continha {resultado_zip['total_arquivos']} arquivo(s) .xml, "
                f"{resultado_zip['total_processados']} processado(s) com sucesso, "
                f"{resultado_zip['total_erros']} com erro de leitura. "
                f"Nenhum item de produto foi encontrado nas notas."
            )
            if resultado_zip["total_arquivos"] == 0:
                detalhe = "O ZIP enviado não contém nenhum arquivo .xml (verifique se o .zip não tem apenas pastas ou outro tipo de arquivo dentro)."
            elif resultado_zip["erros"]:
                detalhe += f" Primeiro erro: {resultado_zip['erros'][0]['erro']}"

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

        print(f"[lote {lote_id}] gerando planilha...")
        caminho_planilha = str(PASTA_ARQUIVOS / f"{lote_id}.xlsx")
        gerar_planilha_resultado(resultados, caminho_planilha)

        lote.status = "concluido"
        lote.total_itens = len(resultados)
        lote.total_com_beneficio_icms = sum(1 for r in resultados if r.tem_beneficio_icms)
        lote.total_com_beneficio_piscofins = sum(1 for r in resultados if r.tem_beneficio_piscofins)
        lote.total_alertas = sum(1 for r in resultados if r.alerta)
        lote.caminho_planilha = caminho_planilha
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

    # Responde imediatamente com status "processando" e deixa o trabalho
    # pesado (ler XMLs, cruzar contra as bases, gerar Excel) rodar depois
    # da resposta HTTP ser enviada — assim o upload não trava a API para
    # outros usuários enquanto processa um lote grande.
    background_tasks.add_task(_processar_lote_em_background, lote.id, caminho_zip)

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
