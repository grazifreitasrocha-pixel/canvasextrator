# Sistema de Análise Fiscal — Versão Web

Backend (FastAPI) + Frontend (HTML/JS) + banco de dados. Suporta múltiplos
usuários, upload pela tela, histórico de lotes processados e download da
planilha final.

## Estrutura

```
sistema-fiscal-web/
├── backend/
│   ├── app/
│   │   ├── main.py              — API (login, upload, processamento, download)
│   │   ├── parser_nfe.py        — extrai NCM/descrição dos XMLs
│   │   ├── motor_analise.py     — cruza contra TIPI/benefícios
│   │   ├── gerar_planilha.py    — gera o Excel final
│   │   ├── ...                  — demais módulos já testados
│   │   └── bases/                — TIPI, benefícios ICMS, PIS/COFINS (JSON)
│   ├── requirements.txt
│   └── Procfile                  — comando de start (usado pelo Railway)
└── frontend/
    └── index.html                 — tela única (login + upload + lotes)
```

## Rodando localmente (para testar antes do deploy)

**Backend:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd app
uvicorn main:app --reload
```
A API sobe em `http://localhost:8000`.

**Frontend** (em outro terminal):
```bash
cd frontend
python3 -m http.server 8080
```
Acesse `http://localhost:8080` no navegador. Como o `index.html` aponta
para `http://localhost:8000` por padrão, os dois já conversam entre si
localmente sem configuração extra.

## Deploy no Railway (recomendado — simples e barato)

### 1. Suba o código para o GitHub
Crie um repositório e suba a pasta `sistema-fiscal-web` inteira.

### 2. Crie o projeto no Railway
1. Acesse [railway.app](https://railway.app) e crie uma conta (pode entrar com GitHub)
2. Clique em **New Project** → **Deploy from GitHub repo**
3. Selecione o repositório que você acabou de subir
4. Quando o Railway perguntar o **Root Directory**, aponte para `backend`

### 3. Adicione o banco de dados PostgreSQL
1. Dentro do projeto no Railway, clique em **New** → **Database** → **PostgreSQL**
2. O Railway cria automaticamente a variável `DATABASE_URL` e já disponibiliza
   para o serviço do backend — não precisa copiar/colar nada manualmente

### 4. Configure a variável de segurança
No serviço do backend, vá em **Variables** e adicione:
```
SECRET_KEY=uma-string-longa-e-aleatoria-só-sua
```
(qualquer texto longo e difícil de adivinhar serve; é usado para assinar os tokens de login)

### 5. Deploy
O Railway já detecta o `Procfile` e sobe o backend automaticamente. Você
vai receber uma URL pública tipo `https://seu-projeto.up.railway.app`.

### 6. Publique o frontend
O jeito mais simples: **Vercel** ou **Netlify** (ambos gratuitos para esse
uso), apontando para a pasta `frontend`. Antes de subir, edite o
`index.html` e troque a linha:
```js
const API_URL = window.API_URL || "http://localhost:8000";
```
por:
```js
const API_URL = "https://seu-projeto.up.railway.app";
```
(usando a URL real que o Railway te deu no passo 5)

Alternativa mais simples ainda (sem separar frontend/backend em serviços
diferentes): o FastAPI também consegue servir o `index.html` diretamente
— se preferir esse caminho mais simples, me avisa que eu ajusto o `main.py`
para isso.

## Custo estimado

Railway: geralmente entre US$5–15/mês para esse volume de uso (backend +
Postgres), cobrado por consumo real. Vercel/Netlify para o frontend
estático: gratuito na maioria dos casos de uso deste tamanho.

## O que ainda falta antes de usar com a equipe toda

- **Base de ICMS-ST** ainda vazia (falta os Apêndices do Anexo VIII)
- **Processamento é síncrono** — para ZIPs muito grandes (milhares de
  notas), o ideal é migrar para uma fila de processamento em background
  antes de escalar para muitos usuários simultâneos
- **CORS está liberado para todos os domínios** (`allow_origins=["*"]`) —
  antes de ir para produção de verdade, restrinja para o domínio real do
  frontend, por segurança
